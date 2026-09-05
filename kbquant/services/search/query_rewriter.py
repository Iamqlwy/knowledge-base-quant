"""阶段2: 查询改写 - 同义词扩展 + ImpactPathService 关系图扩展 + 停用词过滤。

扩展渠道:
1. 实体 alias + ticker (来自阶段1)
2. ImpactPathService 关系图遍历 (有实体时)
3. 静态同义词/反义词词典 (非实体词)
4. 停用词过滤
"""
import asyncio
import logging
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

from kbquant.models.search_candidate import SearchContext

logger = logging.getLogger(__name__)

_SYNONYMS_PATH = Path(__file__).parent.parent.parent / "assets" / "finance_synonyms.txt"
_STOPWORDS_PATH = Path(__file__).parent.parent.parent / "assets" / "stopwords.txt"

# Default timeout for ImpactPathService calls (seconds)
_IMPACT_PATH_TIMEOUT = 3.0

# Regex to split Chinese text into words — matches CJK character sequences and
# ASCII word tokens, so Chinese stopwords can be filtered without requiring
# whitespace between characters.
_CJK_WORD_RE = re.compile(r'[一-鿿㐀-䶿]+|[a-zA-Z0-9]+')

# Similarity threshold for filtering near-identical aliases (e.g. "智能电网" vs "智能电网概念")
_ALIAS_SIMILARITY_THRESHOLD = 0.7

# Time bias: (keyword, days) pairs sorted by specificity (more specific first)
_TIME_BIAS_RULES: list[tuple[list[str], int]] = [
    (["今天", "刚刚", "今日"], 3),
    (["昨日", "昨天"], 7),
    (["本周"], 14),
    (["最近", "最新", "近期", "近日"], 30),
    (["本月"], 60),
    (["财报", "季报", "年报", "半年报"], 90),
    (["今年", "本年度"], 180),
]


class QueryRewriter:
    def __init__(self, synonyms_path: str | None = None,
                 stopwords_path: str | None = None,
                 impact_path_service=None):
        self._synonyms: dict[str, list[str]] = {}
        self._reverse_synonyms: dict[str, str] = {}
        self._stopwords: set[str] = set()
        self._impact_path_service = impact_path_service

        self._load_synonyms(synonyms_path or str(_SYNONYMS_PATH))
        self._load_stopwords(stopwords_path or str(_STOPWORDS_PATH))

    def _load_synonyms(self, path: str):
        if not os.path.exists(path):
            logger.warning("synonyms file not found: %s", path)
            return

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if ":" in line:
                    term, synonyms_str = line.split(":", 1)
                    term = term.strip().lower()
                    synonyms = [s.strip().lower() for s in synonyms_str.split(",") if s.strip()]
                    self._synonyms[term] = synonyms
                    for s in synonyms:
                        self._reverse_synonyms[s] = term

        logger.debug("loaded synonyms: %d entries", len(self._synonyms))

    def _load_stopwords(self, path: str):
        if not os.path.exists(path):
            logger.warning("stopwords file not found: %s", path)
            return

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                self._stopwords.add(line.lower())

        logger.debug("loaded stopwords: %d", len(self._stopwords))

    async def rewrite(
        self,
        query_text: str,
        ctx: SearchContext | None = None,
    ) -> dict:
        query_text_lower = query_text.lower()

        _seen: set[str] = set()
        _ordered: list[str] = []

        def _add(word: str):
            if word and word not in _seen:
                _seen.add(word)
                _ordered.append(word)

        # extract entity names for later dedup
        entity_names: set[str] = set()
        if ctx and ctx.entities:
            for entity in ctx.entities:
                entity_names.add(entity.name.lower())
                for alias in entity.aliases:
                    entity_names.add(alias.lower())

        # 0. entity names first
        if ctx and ctx.entities:
            for entity in ctx.entities:
                _add(entity.name.lower())

        # 0.5 time bias inference — run before keyword extraction so
        # temporal qualifiers (今日/昨天/本周 etc.) are excluded from
        # search keywords after they've been used for time_bias.
        time_bias_days, time_bias_keywords = self._infer_time_bias(query_text_lower)

        # 1. query words (filter stopwords, skip words already covered by entities)
        raw_tokens = _CJK_WORD_RE.findall(query_text_lower)
        query_words = [w for w in raw_tokens
                       if w not in self._stopwords
                       and w not in time_bias_keywords
                       and w not in entity_names]
        for w in query_words:
            _add(w)

        # 2. entity aliases (only those substantially different from the name)
        if ctx and ctx.entities:
            for entity in ctx.entities:
                for alias in entity.aliases:
                    if SequenceMatcher(None, entity.name.lower(), alias.lower()).ratio() < _ALIAS_SIMILARITY_THRESHOLD:
                        _add(alias.lower())

        # 3. ImpactPathService relationship graph expansion (with timeout)
        entity_context: dict[str, float] = {}
        if self._impact_path_service and ctx and ctx.entities:
            await self._expand_via_impact_path(ctx.entities, _add, entity_context)

        # 4. non-entity word synonym expansion
        for word in query_words:
            if word in entity_names:
                continue
            if word in self._synonyms:
                filtered = self._filter_synonyms_by_entity_ctx(word, self._synonyms[word], ctx)
                for s in filtered:
                    _add(s)
            if word in self._reverse_synonyms:
                canonical = self._reverse_synonyms[word]
                _add(canonical)
                filtered = self._filter_synonyms_by_entity_ctx(canonical, self._synonyms.get(canonical, []), ctx)
                for s in filtered:
                    _add(s)

        if ctx is not None:
            ctx.expanded_keywords = set(_ordered)
            ctx.entity_context = entity_context
            ctx.time_bias_days = time_bias_days
            ctx.temporal_keywords = time_bias_keywords

        return {
            "original": query_text,
            "expanded_keywords": _ordered,
            "entity_context": entity_context,
            "time_bias_days": time_bias_days,
        }

    async def _expand_via_impact_path(
        self,
        entities: list,
        _add,
        entity_context: dict[str, float],
    ):
        """Call ImpactPathService with timeout protection."""
        if not self._impact_path_service:
            return

        for entity in entities:
            if entity.entity_type not in ("stock", "company", "tech_company", "concept", "sector", "industry", "key_technology"):
                continue
            try:
                result = await asyncio.wait_for(
                    self._impact_path_service.find_paths_by_name(
                        entity.name,
                        depth=2,
                        direction="both",
                    ),
                    timeout=_IMPACT_PATH_TIMEOUT,
                )
                if not result or not result.get("paths"):
                    continue
                for path_info in result["paths"]:
                    strength = path_info.get("total_impact_strength", 0.0)
                    path_nodes = path_info.get("path", [])
                    if not path_nodes:
                        continue
                    target = path_nodes[-1]
                    target_name = target.get("entity_name", "")
                    if not target_name or target_name.lower() == entity.name.lower():
                        continue
                    if strength >= 0.4:
                        _add(target_name.lower())
                    else:
                        entity_context[target_name] = round(strength, 4)
            except asyncio.TimeoutError:
                logger.debug(
                    "ImpactPathService timed out for %s (%.1fs)",
                    entity.name, _IMPACT_PATH_TIMEOUT,
                )
            except Exception as exc:
                logger.debug(
                    "ImpactPathService query failed for %s: %s",
                    entity.name, exc,
                )

    @staticmethod
    def _infer_time_bias(query_text: str) -> tuple[int | None, set[str]]:
        """Infer recency bias from query text. Rules are checked in priority
        order (more specific first), first match wins.
        Returns (days, matched_keywords)."""
        for keywords, days in _TIME_BIAS_RULES:
            matched = {kw for kw in keywords if kw in query_text}
            if matched:
                return days, matched
        return None, set()

    @staticmethod
    def _filter_synonyms_by_entity_ctx(
        word: str,  # noqa: ARG004 (informational — used for debug context)
        synonyms: list[str],
        ctx: SearchContext | None,
    ) -> list[str]:
        """Filter synonyms that conflict with the entity type context.

        When entities resolve to stock/company/tech_company, filter out
        location-named synonyms (city/province/street suffixes) that would
        cause false matches against company names.
        """
        if not ctx or not ctx.entities:
            return synonyms

        entity_types = {e.entity_type for e in ctx.entities if e.entity_type}
        if not (entity_types & {"stock", "company", "tech_company"}):
            return synonyms

        blocked_chars = {"市", "省", "县", "区", "街", "路", "镇", "乡"}
        return [
            s for s in synonyms
            if not (2 <= len(s) <= 4 and s[-1] in blocked_chars)
        ]

