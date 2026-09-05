"""阶段1: 实体识别 — 从搜索查询中提取实体并确定主实体。

复用 matcher.py 的 EntityMatcher（Aho-Corasick 自动机扫描）
和 search_service.py 的 entity_match_search（WorldNode 精确匹配）。
"""
import json
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field

from kbquant.pipeline.matcher import EntityMatcher
from kbquant.models.search_candidate import EntityResult, SearchContext

logger = logging.getLogger(__name__)

# Module-level singleton with thread-safe double-checked locking
_entity_matcher: EntityMatcher | None = None
_matcher_lock = threading.Lock()

_IDF_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "idf_cache.json"


def _load_idf_cache() -> dict[str, float]:
    if not _IDF_CACHE_PATH.exists():
        logger.debug("IDF cache not found at %s", _IDF_CACHE_PATH)
        return {}
    try:
        with open(_IDF_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.debug("Failed to load IDF cache", exc_info=True)
        return {}


def get_entity_matcher() -> EntityMatcher:
    global _entity_matcher
    if _entity_matcher is None:
        with _matcher_lock:
            if _entity_matcher is None:
                idf_cache = _load_idf_cache()
                _entity_matcher = EntityMatcher(idf_cache=idf_cache)
    return _entity_matcher

# 实体类型优先级（越低越优先作为主实体）
_ENTITY_TYPE_PRIORITY = {
    "company": 0,
    "tech_company": 0,
    "index": 1,
    "sector": 1,
    "concept": 1,
    "key_technology": 1,
    "commodity": 3,
    "strategy": 4,
    "geopolitical_event": 5,
    "epidemic": 5,
    "person": 7,
    "institution": 8,
    "research_institution": 8,
    "policy": 9,
    "event": 10,
    "semiconductor_term": 11,
    "natural_disaster": 12,
}

# Configurable thresholds for main-entity determination.
# Overridable via EntityResolver(..., main_thresholds={...}).
_DETERMINE_MAIN_CONFIG = {
    "stock_high": 0.8,       # single stock/company must reach this to be main
    "stock_multi": 0.6,      # 2+ stocks at this threshold → multi_stock mode
    "strategy": 0.8,
    "industry": 0.5,
    "person": 0.8,
}


@dataclass
class ResolvedEntity:
    name: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)
    ticker: str | None = None
    match_method: str = ""
    priority: int = 100
    score: float = 0.0
    node_id: str | None = None  # WorldNode UUID for cross-channel dedup

    def to_entity_result(self) -> EntityResult:
        return EntityResult(
            name=self.name,
            entity_type=self.entity_type,
            aliases=self.aliases,
            ticker=self.ticker,
            match_method=self.match_method,
            score=self.score,
            node_id=self.node_id,
        )


class EntityResolver:
    """阶段1: 从搜索查询中识别实体。

    两阶段匹配：
    1. Aho-Corasick 自动机扫描 — 基于 matcher.py 的 EntityMatcher
    2. WorldNode 精确匹配（ticker/name/alias）— 基于现有 _entity_match_search
    3. 合并去重 + 按规范阈值判定主实体
    """

    def __init__(self, entity_matcher: EntityMatcher | None = None,
                 main_thresholds: dict | None = None):
        self._matcher = entity_matcher or get_entity_matcher()
        self._main_thresholds = main_thresholds or _DETERMINE_MAIN_CONFIG

    async def resolve(
        self,
        query_text: str,
        session=None,
        ctx: SearchContext | None = None,
    ) -> list[ResolvedEntity]:
        entities: list[ResolvedEntity] = []

        # Phase 1a: Aho-Corasick 自动机扫描 with position-aware TF-IDF scoring.
        # Uses EntityScorer (pipeline/scoring.py) which accounts for:
        # - TF (log-normalized occurrences)
        # - IDF (corpus-level rarity for generic types)
        # - position (earlier = more important)
        # - density (hits per char)
        # - type priors (person > company > sector > region)
        # - gap cutoff (stops at importance cliff)
        ac_matches = self._matcher.match_with_scores(query_text, title="", max_entities=20)
        for m in ac_matches:
            entity_type = m.get("entity_type", "")
            name = m.get("name", "")
            priority = _ENTITY_TYPE_PRIORITY.get(entity_type, 100)
            # importance is already a normalized [0, ~1] score from EntityScorer
            score = float(m.get("importance", 0.0))
            # Collect matched terms that differ from the canonical name as aliases
            matched_terms = m.get("matched_terms", [])
            ac_aliases = [t for t in matched_terms if t.lower().strip() != name.lower().strip()]
            entities.append(ResolvedEntity(
                name=name,
                entity_type=entity_type,
                aliases=ac_aliases,
                ticker=None,
                match_method="aho_corasick",
                priority=priority,
                score=score,
            ))

        # Phase 1b: WorldNode 精确匹配（ticker/name/alias）
        if session is not None:
            try:
                # Single batched query: merge all token conditions via OR
                # into one query instead of N parallel queries.
                from sqlalchemy import or_, select
                from kbquant.models.world_node import WorldNode

                query_tokens = [t for t in query_text.strip().split() if len(t) >= 2]
                if not query_tokens:
                    query_tokens = [query_text.strip()]
                query_tokens = query_tokens[:5]

                # Build OR-ed ilike conditions for all tokens at once
                token_clauses: list = []
                for token in query_tokens:
                    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    token_clauses.extend([
                        WorldNode.name.ilike(f"{escaped}%"),
                        WorldNode.ticker.ilike(f"{escaped}%"),
                        WorldNode.name.ilike(f"%{escaped}%"),
                        WorldNode.ticker.ilike(f"%{escaped}%"),
                        WorldNode.aliases.any(token),
                        WorldNode.aliases.any(f"%{escaped}%"),
                    ])
                result = await session.execute(
                    select(WorldNode)
                    .where(WorldNode.is_active == True, or_(*token_clauses))
                    .limit(50)
                )
                rows = result.scalars().all()

                # Score each matched node using the same logic as before
                query_stripped = query_text.strip()
                query_lower = query_stripped.lower()
                for row in rows:
                    entity_type = getattr(row, "node_type", "") or ""
                    name = getattr(row, "name", "") or ""
                    ticker = getattr(row, "ticker", "") or None
                    aliases = list(getattr(row, "aliases", []) or [])
                    node_id = str(row.id) if hasattr(row, "id") else str(row.id)

                    name_lower = (name or "").lower().strip()
                    ticker_val = (ticker or "").lower().strip()
                    aliases_lower = [a.lower().strip() for a in (aliases or [])]

                    if ticker_val and ticker_val == query_lower:
                        match_score = 1.2
                        method = "worldnode_ticker_exact"
                    elif name_lower == query_lower:
                        match_score = 1.0
                        method = "worldnode_score"
                    elif query_lower in aliases_lower:
                        match_score = 0.95
                        method = "worldnode_score"
                    elif name_lower.startswith(query_lower) and len(query_lower) >= 3:
                        match_score = 0.8
                        method = "worldnode_score"
                    elif any(query_lower in a for a in aliases_lower):
                        match_score = 0.7
                        method = "worldnode_score"
                    elif query_lower in name_lower:
                        match_score = 0.5
                        method = "worldnode_score"
                    else:
                        match_score = 0.3
                        method = "worldnode_score"

                    if ticker_val and entity_type not in ("stock", "company"):
                        entity_type = "company"

                    if ticker_val and (query_stripped.isdigit() or query_stripped.isalnum()):
                        q_lower = query_lower
                        if ticker_val == q_lower or ticker_val.startswith(q_lower):
                            match_score = max(match_score, 1.2)
                            method = "worldnode_ticker_exact"

                    if ticker:
                        aliases = list(set(aliases + [ticker]))

                    priority = _ENTITY_TYPE_PRIORITY.get(entity_type, 100)

                    entities.append(ResolvedEntity(
                        name=name,
                        entity_type=entity_type,
                        aliases=aliases,
                        ticker=ticker,
                        match_method=method,
                        priority=priority,
                        score=float(match_score),
                        node_id=node_id,
                    ))
            except Exception:
                logger.debug("WorldNode 实体匹配失败", exc_info=True)

        # 合并去重：按 name 合并 aliases、ticker、entity_type 和 node_id。
        # Scoring policy: AC position-aware importance is the primary signal.
        # WorldNode match provides node_id + entity_type anchoring — score is
        # kept as metadata but merged entity uses max(ac_score, wn_score).
        merged: dict[str, ResolvedEntity] = {}
        for e in entities:
            key = e.name.lower().strip()
            if key in merged:
                existing = merged[key]
                existing.aliases = list(set(existing.aliases + e.aliases))
                if not existing.ticker and e.ticker:
                    existing.ticker = e.ticker
                if not existing.node_id and e.node_id:
                    existing.node_id = e.node_id
                # WorldNode contributions (node_id, ticker, entity_type) are
                # always preserved; score takes the best of either channel.
                existing.score = max(existing.score, e.score)
                if e.match_method in ("worldnode_score", "worldnode_ticker_exact"):
                    existing.match_method = e.match_method
                    existing_pri = _ENTITY_TYPE_PRIORITY.get(existing.entity_type, 100)
                    new_pri = _ENTITY_TYPE_PRIORITY.get(e.entity_type, 100)
                    if new_pri <= existing_pri or existing.entity_type == "":
                        existing.entity_type = e.entity_type
                    if e.node_id:
                        existing.node_id = e.node_id
            else:
                merged[key] = e

        # 按优先级 + 得分排序
        result = sorted(merged.values(), key=lambda e: (e.priority, -e.score))

        # 按规范判定主实体
        main_entity, is_multi_stock = self._determine_main(result)

        if ctx is not None:
            ctx.entities = [e.to_entity_result() for e in result]
            # Cache WorldNode data for ConceptNodeInjectionRule (avoids extra DB session)
            ctx._resolved_worldnodes = [
                {"id": e.node_id, "name": e.name, "description": e.entity_type, "updated_at": None}
                for e in result if e.node_id
            ]
            if main_entity:
                main_result = main_entity.to_entity_result()
                if is_multi_stock:
                    main_result.aliases = list(set(
                        main_result.aliases + ["multi_stock"]
                    ))
                ctx.main_entity = main_result
            else:
                ctx.main_entity = None

        logger.info(
            "entity_resolver: query=%r entities=%d main=%s(%s) score=%.2f multi_stock=%s",
            query_text[:60],
            len(result),
            main_entity.name if main_entity else "none",
            main_entity.entity_type if main_entity else "",
            main_entity.score if main_entity else 0.0,
            is_multi_stock,
        )
        for e in result[:5]:
            logger.info(
                "entity_resolver detail: name=%s type=%s score=%.2f ticker=%s method=%s",
                e.name, e.entity_type, e.score, e.ticker, e.match_method,
            )
        return result

    def _determine_main(
        self,
        resolved: list[ResolvedEntity],
    ) -> tuple[ResolvedEntity | None, bool]:
        """按规范判定主实体。"""
        if not resolved:
            return None, False

        cfg = self._main_thresholds

        # 规则1 & 2: company/tech_company 优先
        stock_entities = [
            e for e in resolved
            if e.entity_type in ("company", "tech_company")
        ]
        high_stocks = [e for e in stock_entities if e.score >= cfg["stock_high"]]
        if high_stocks:
            return high_stocks[0], False

        mid_stocks = [e for e in stock_entities if e.score >= cfg["stock_multi"]]
        if len(mid_stocks) >= 2:
            return mid_stocks[0], True  # multi_stock

        if len(mid_stocks) == 1:
            return mid_stocks[0], False

        # 规则3: strategy
        for e in resolved:
            if e.entity_type == "strategy" and e.score >= cfg["strategy"]:
                return e, False

        # 规则4: sector/concept/key_technology/geopolitical_event
        for e in resolved:
            if e.entity_type in ("sector", "concept", "key_technology", "geopolitical_event") and e.score >= cfg["industry"]:
                return e, False

        # 规则5: person
        for e in resolved:
            if e.entity_type == "person" and e.score >= cfg["person"]:
                return e, False

        return None, False
