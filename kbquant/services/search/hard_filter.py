"""阶段4: 硬过滤 - 基于实体约束和向量下限过滤不相关候选。

规则:
1. Entity 硬约束 (DROP): 存在实体时，候选必须命中至少一个实体的 name/ticker/alias
   否则直接丢弃——不区分 stock/non-stock，一视同仁
2. 向量分数下限 (DROP): 仅向量命中且分数 < threshold 的候选被丢弃
3. 关键词覆盖 (DEMOTE): 扩展关键词≥3 时，按命中比率连续性降权
4. 时间范围过滤 (DROP): 对仅向量命中的结果检查 date_range
5. 行情数据过滤 (DROP/DEMOTE): 当查询非行情意图时，raw_information 中
   标题命中行情关键词的候选直接丢弃，正文命中的降权处理
6. Entity Context 信号已移入 FusionService（这里不再重复）

_extract_text_from_result 修复后 nodes 不再豁免实体硬约束：
  所有字段（包括 state_summary、core_logic 和 JSONB 字段）现已被拼接，
  实体名称匹配依赖的是提取后的完整文本，而非之前有缺陷的 or 链提取。

_extract_text_from_result 修复：
  早期版本使用 Python `or` 链，只能从 NodeState 行中提取一个字段
  （例如 state_summary 存在时 core_logic 会被丢失）。所有字段现已被拼接。
  NodeState 上的 JSONB 字段（primary_drivers、risks 等）现已被包含。
  实体名称匹配依赖的是提取后的完整文本，而非之前有缺陷的 or 链。
"""

import logging
import re
from datetime import datetime, timezone

from kbquant.models.search_candidate import Candidate, SearchContext, EntityResult
from kbquant.utils.text import word_boundary_match

logger = logging.getLogger(__name__)

VECTOR_LOW_THRESHOLD = 0.3
KEYWORD_COVERAGE_MIN_COUNT = 3
DEMOTE_MULTIPLIER = 0.5

# Per-table vector quality thresholds.
# raw_information has the largest index and best embedding quality → lower threshold.
# nodes uses NodeState embeddings which can be noisier → higher threshold.
_DEFAULT_VECTOR_THRESHOLDS: dict[str, float] = {
    "raw_information": 0.25,
    "analyses": 0.30,
    "feedbacks": 0.30,
    "nodes": 0.35,
}

# ── 行情数据过滤关键词 ────────────────────────────────────────────────

# 行情标题正则：raw_information 标题命中任一 → 直接丢弃（强信号）
# 用正则而非字面关键词，因为行情标题模式多样：
#   "369股获融资买入超亿元"  "新易盛跌近10%"  "源杰科技涨超10%"
#   "世嘉科技涨停"  "华盛昌触及涨停"  "获买入25.39亿元居首"
_MARKET_DATA_TITLE_RE: tuple = (
    # N stocks got financing: "369股获融资买入"
    re.compile(r'\d+股获(?:融资)?买入'),
    # Buying amount summaries: "获买入40.85亿元居首", "获融资买入超亿元"
    re.compile(r'获(?:融资)?买入\d+\.?\d*[亿万]'),
    re.compile(r'获融资买入'),
    # Price change with percent: "跌近10%", "涨超10%", "跌逾3%"
    re.compile(r'[涨跌](?:超|近|破|逾|达|扩大至|幅)\d+\.?\d*[%％]'),
    # "涨幅扩大至6%", "跌幅收窄至2%"
    re.compile(r'[涨跌]幅(?:扩大|收窄)至\d+'),
    # Bare price change at sentence end: "涨4%", "跌6%"
    re.compile(r'[涨跌]\d+\.?\d*[%％]'),
    # N天M板: "6天3板", "10天8板"
    re.compile(r'\d+天\d+板'),
    # N连板: "5连板", "4连板" (consecutive daily limit-ups)
    re.compile(r'\d+连板'),
    # Limit up/down: "触及涨停", "封涨停", "一字跌停", "再度涨停"
    re.compile(r'(?:触及|封|一字|开盘|尾盘|盘中|再[度次])?[涨跌]停'),
    # Pure market data summary/digest titles
    re.compile(r'(?:行情|盘后|盘前|收盘|开盘)(?:日报|周报|总结|综述|必读|速递|快讯)'),
    # Volume/flow/turnover rankings or summaries
    re.compile(r'(?:成交量|换手率|成交额|主力资金流向)(?:排名|统计|日报)?'),
    re.compile(r'龙虎榜'),
    # Market movement summaries: "板块震荡走强", "概念股活跃", "盘初走强",
    # "创新药板块表现活跃" (allow up to 4 chars between 板块 and the movement word)
    re.compile(r'(?:板块|概念股|盘初|盘中|尾盘)(?:.{0,4})?(?:震荡|走强|走弱|拉升|下挫|跳水|异动|活跃|创新高|创新低)'),
    # Following the rise: "跟涨", "涨幅居前", "跌幅居前", "涨超10%"
    re.compile(r'(?:跟涨|涨幅居前|跌幅居前|领涨|领跌|涨超\d+)'),
    # Price change with yuan: "涨超10元", "跌超5元" (less common but possible)
    re.compile(r'[涨跌](?:超|近|破|逾)\d+\.?\d*元'),
    # Individual stock limit boards as standalone event: "涨停", "跌停" in title position
    re.compile(r'(?:涨停|跌停)(?:开盘|封板|打开|打开涨停|打开跌停)?'),
    # Turnover / volume surge: "放量", "缩量"
    re.compile(r'(?:放量|缩量|天量|地量)(?:涨停|跌停|上涨|下跌|拉升)?'),
    # Index movement: "沪指涨", "创业板指跌"
    re.compile(r'(?:沪指|深成指|创业板指|科创50|恒指|恒生|道指|纳指|标普)[涨跌]'),
    # Broad market: "A股", "港股", "美股" + movement
    re.compile(r'(?:A股|港股|美股|欧股|日股)(?:开盘|收盘|午盘|早盘|尾盘|盘中)?(?:走强|走弱|上涨|下跌|反弹|回调|震荡)'),
)

# 行情正文关键词：raw_information 正文命中任一（但标题未命中）→ apply_boosts 降权
_MARKET_DATA_BODY_KEYWORDS: frozenset[str] = frozenset({
    "涨跌幅", "换手率", "量比", "委比", "振幅",
    "主力净流入", "大单净流入", "超大单", "大单", "中单", "小单",
    "成交量", "成交额", "总市值", "流通市值",
    "龙虎榜", "资金流向",
})

_MARKET_DATA_DEMOTE_MULT = 0.6


class HardFilter:
    """阶段4: 硬过滤候选结果。

    两个入口:
    - filter_raw_results: RRF 融合前过滤原始召回结果
    - apply_boosts: RRF 融合后施加降权规则
    """

    def __init__(
        self,
        vector_low_threshold: float = VECTOR_LOW_THRESHOLD,
        demote_multiplier: float = DEMOTE_MULTIPLIER,
        vector_thresholds: dict | None = None,
    ):
        self.vector_low_threshold = vector_low_threshold
        self.vector_thresholds = vector_thresholds or _DEFAULT_VECTOR_THRESHOLDS
        self.demote_multiplier = demote_multiplier

    # ── Public API ───────────────────────────────────────────────────

    def filter_raw_results(
        self,
        bm25_results: dict[str, dict],
        pg_results: dict[str, dict],
        name_match_results: dict[str, dict],
        ctx: SearchContext | None = None,
    ) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], dict[str, int]]:
        """RRF 融合前过滤原始结果。

        Returns:
            (bm25_results, pg_results, name_match_results, drop_counts)
        """
        dropped: dict[str, int] = {}
        date_range = ctx.date_range if ctx else None

        # Rule 1: Entity hard constraint — 存在任何实体时，候选必须命中
        # 至少一个实体的 name/ticker/alias，否则直接丢弃。
        if ctx and ctx.entities:
            bm25_results = self._drop_by_entities(bm25_results, ctx, dropped)
            pg_results = self._drop_by_entities(pg_results, ctx, dropped)
            # name_match results are already entity-linked (queried by node_id
            # with relevance_score >= 0.5) — skip entity filter for them.

        # Rule 1.5: Market data filter — when the query intent is NOT
        # market_data, drop raw_information candidates whose titles match
        # market-data keywords (e.g. "行情日报", "龙虎榜", "收盘价").
        bm25_results, pg_results = self._drop_market_data_titles(
            bm25_results, pg_results, ctx, dropped,
        )

        # Rule 1.6: Title-based near-duplicate dedup — applies to
        # raw_information, analyses, and feedbacks (nodes are never deduped).
        # Within each cluster of similar titles, keep only the most recent.
        # Skipped for market_data intent — users explicitly want many quotes.
        intent = ctx.timings.get("dynamic_weights_intent", "general") if ctx else "general"
        if intent != "market_data":
            bm25_results, pg_results = self._dedup_by_title(
                bm25_results, pg_results, dropped,
            )

        # Rule 2: Vector low threshold
        pg_results, vec_dropped = self._filter_low_vector(pg_results)
        dropped["vector_low"] = vec_dropped

        # Rule 3: Node active check — drop nodes whose WorldNode is inactive
        # or not found. ES sync is async; a node may be deactivated after
        # the ES index was written or the node_id may no longer be valid.
        bm25_results = self._drop_inactive_nodes(bm25_results, dropped)
        pg_results = self._drop_inactive_nodes(pg_results, dropped)
        name_match_results = self._drop_inactive_nodes(name_match_results, dropped)

        # Rule 4: Date range for vector-only results
        # BM25 already has date_range in ES filter context; name_match has
        # no date (entities are timeless), so only pg_results is checked.
        if date_range:
            pg_results, dr_dropped = self._filter_vector_by_date(pg_results, date_range)
            if dr_dropped:
                dropped["date_range"] = dr_dropped

        if ctx is not None:
            ctx.filtered_count = dropped

        return bm25_results, pg_results, name_match_results, dropped

    def apply_boosts(
        self,
        candidates: list[Candidate],
        ctx: SearchContext | None = None,
    ) -> list[Candidate]:
        """RRF 融合后施加降权规则（soft 规则，不改变排序结构）。

        实体约束已在 filter_raw_results 中走硬 DROP，这里处理关键词覆盖降权
        和行情正文关键词降权。
        """
        if not candidates:
            return candidates

        # ── Market-data body keyword demotion ──
        # raw_information candidates whose body (not title) contains market-data
        # keywords get a penalty, unless the user is explicitly querying for it.
        intent = ctx.timings.get("dynamic_weights_intent", "general") if ctx else "general"
        if intent != "market_data":
            market_body_demoted = 0
            for c in candidates:
                if c.result_type != "raw_information":
                    continue
                text = (c.snippet or "").lower()
                if any(kw in text for kw in _MARKET_DATA_BODY_KEYWORDS):
                    c.penalty_mult = min(c.penalty_mult, _MARKET_DATA_DEMOTE_MULT)
                    market_body_demoted += 1
            if market_body_demoted:
                logger.info(
                    "hard_filter=market_data_body intent=%s demoted=%d/%d",
                    intent, market_body_demoted, len(candidates),
                )

        expanded_keywords = ctx.expanded_keywords if ctx else set()
        kw_count = len(expanded_keywords)

        if kw_count < KEYWORD_COVERAGE_MIN_COUNT:
            return candidates

        for c in candidates:
            full_body = HardFilter._extract_text_from_result({
                "source": c.es_source,
                "row": c.raw,
            })
            full_text = (c.title + " " + full_body).lower()

            # ── Rule 3: Continuous keyword coverage demote ──
            # coverage 0.0 → multiplier 0.5
            # coverage 0.5 → multiplier 0.75
            # coverage 1.0 → multiplier 1.0
            hit_count = sum(1 for kw in expanded_keywords if kw.lower() in full_text)
            coverage = hit_count / max(kw_count, 1)
            penalty_mult = 1.0 - (1.0 - coverage) * self.demote_multiplier
            c.penalty_mult = min(c.penalty_mult, penalty_mult)

        return candidates

    # ── Private helpers ──────────────────────────────────────────────

    @staticmethod
    def _extract_text_from_result(info: dict) -> str:
        """从检索结果字典中提取全部可搜索文本。

        将 source 或 row 中的所有相关字段拼接为一个字符串，
        包括 NodeState 上的 JSONB 字段（primary_drivers、risks 等）。
        `or` 链已被替换为拼接，使得所有字段都被包含，
        而非仅有第一个具有值的字段。
        """
        source = info.get("source", {})
        if isinstance(source, dict) and source:
            source_parts = [
                source.get("title", ""),
                source.get("body", ""),
                source.get("content", ""),
                source.get("name", ""),
                source.get("description", ""),
                source.get("node_type", ""),
                source.get("core_logic", ""),
                source.get("state_summary", ""),
                source.get("lessons_learned", ""),
            ]
            return " ".join(p for p in source_parts if p).lower()
        if isinstance(source, str) and source:
            return source.lower()
        row = info.get("row")
        if isinstance(row, dict):
            title_parts = [row.get("title", ""), row.get("name", "")]
            body_parts = [
                row.get("body", ""),
                row.get("content", ""),
                row.get("description", ""),
                row.get("state_summary", ""),
                row.get("core_logic", ""),
                row.get("recent_changes", ""),
                row.get("lessons_learned", ""),
            ]
            HardFilter._extend_text_from_jsonb(row, body_parts)
            title = " ".join(t for t in title_parts if t)
            body = " ".join(p for p in body_parts if p)
            return (title + " " + body).lower()
        if row:
            title_parts = [
                getattr(row, "title", None) or "",
                getattr(row, "name", None) or "",
            ]
            body_parts = [
                getattr(row, "body", None) or "",
                getattr(row, "content", None) or "",
                getattr(row, "description", None) or "",
                getattr(row, "state_summary", None) or "",
                getattr(row, "core_logic", None) or "",
                getattr(row, "recent_changes", None) or "",
                getattr(row, "lessons_learned", None) or "",
            ]
            HardFilter._extend_text_from_jsonb(row, body_parts)
            title = " ".join(t for t in title_parts if t)
            body = " ".join(p for p in body_parts if p)
            return (title + " " + body).lower()
        return ""

    @staticmethod
    def _extend_text_from_jsonb(row, body_parts: list[str]) -> None:
        """从 NodeState JSONB 列表列（primary_drivers、risks 等）中追加文本。

        接受行可以是 dict（来自 _pg_vector_search 物化后的结果）或 ORM 对象。
        """
        jsonb_columns = (
            "primary_drivers", "risks", "focus_points", "uncertainty_flags",
        )
        for col in jsonb_columns:
            val = row.get(col) if isinstance(row, dict) else getattr(row, col, None)
            if not val:
                continue
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        for v in item.values():
                            if v and isinstance(v, str):
                                body_parts.append(v)
                    elif isinstance(item, str):
                        body_parts.append(item)

    @staticmethod
    def _drop_market_data_titles(
        bm25_results: dict[str, dict],
        pg_results: dict[str, dict],
        ctx: SearchContext | None,
        dropped: dict[str, int],
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        """Drop raw_information candidates whose titles match market-data keywords.

        Skipped when the query intent is market_data.
        """
        if ctx is None:
            return bm25_results, pg_results

        intent = ctx.timings.get("dynamic_weights_intent", "general") if ctx else "general"
        if intent == "market_data":
            return bm25_results, pg_results

        bm25_dropped = 0
        pg_dropped = 0

        # Determine raw_information pids from pg_results
        pg_raw_pids: set[str] = set()
        for pid, info in pg_results.items():
            row = info.get("row")
            if row is not None:
                if isinstance(row, dict):
                    row_cls = row.get("__class_name__", "")
                else:
                    row_cls = type(row).__name__
                if row_cls not in ("Analysis", "Feedback", "NodeState", "WorldNode"):
                    pg_raw_pids.add(pid)
            else:
                pg_raw_pids.add(pid)

        bm25_out: dict[str, dict] = {}
        for pid, info in bm25_results.items():
            source = info.get("source", {})
            title = ""
            if isinstance(source, dict):
                title = source.get("title", "")
            elif isinstance(source, str):
                title = source
            if title and any(pat.search(title) for pat in _MARKET_DATA_TITLE_RE):
                bm25_dropped += 1
                continue
            bm25_out[pid] = info

        pg_out: dict[str, dict] = {}
        for pid, info in pg_results.items():
            if pid in pg_raw_pids:
                title = ""
                row = info.get("row")
                if isinstance(row, dict):
                    title = row.get("title", "") or row.get("name", "")
                elif hasattr(row, "title") or hasattr(row, "name"):
                    title = (getattr(row, "title", None) or "") or (getattr(row, "name", None) or "")
                if title and any(pat.search(title) for pat in _MARKET_DATA_TITLE_RE):
                    pg_dropped += 1
                    continue
            pg_out[pid] = info

        if bm25_dropped or pg_dropped:
            dropped["market_data_title"] = bm25_dropped + pg_dropped
            logger.info(
                "hard_filter=market_data_title intent=%s bm25_dropped=%d pg_dropped=%d",
                intent, bm25_dropped, pg_dropped,
            )

        return bm25_out, pg_out

    # ── Near-duplicate dedup for raw_information ──────────────────────

    @staticmethod
    def _char_bigrams(text: str) -> set[str]:
        """Extract character-level bigrams for fuzzy title comparison.

        Parenthetical date suffixes like "（2026-04-15）" are stripped first
        so they don't artificially differentiate otherwise identical titles.
        """
        text = HardFilter._TITLE_DATE_STRIP_RE.sub("", text).strip().lower()
        if len(text) < 2:
            return {text}
        return {text[i:i+2] for i in range(len(text) - 1)}

    @staticmethod
    def _bigram_jaccard(a: str, b: str) -> float:
        """Jaccard similarity between the character bigram sets of two strings."""
        ba = HardFilter._char_bigrams(a)
        bb = HardFilter._char_bigrams(b)
        if not ba or not bb:
            return 0.0
        intersection = len(ba & bb)
        union = len(ba | bb)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _extract_title_from_result(info: dict) -> str:
        source = info.get("source")
        if isinstance(source, dict):
            t = source.get("title", "")
            if t:
                return str(t)
        row = info.get("row")
        if isinstance(row, dict):
            t = row.get("title", "") or row.get("name", "")
            if t:
                return str(t)
        elif row is not None:
            t = getattr(row, "title", None) or getattr(row, "name", None) or ""
            return str(t)
        return ""

    # Per-table bigram Jaccard threshold for pre-fusion title dedup.
    # raw_information: aggressive (0.50) — flash quotes differ by one number.
    # analyses / feedbacks: 0.50 — same-event rewrites typically score 0.53–0.60;
    #   genuinely different analyses share < 0.50 even with the same subject.
    # nodes: never deduped.
    _DEDUP_THRESHOLDS: dict[str, float] = {
        "raw_information": 0.50,
        "analyses": 0.50,
        "feedbacks": 0.50,
        "nodes": 1.0,
    }

    # Parenthetical date suffix pattern: "（2026-04-15）" / "(2026-04-15)"
    _TITLE_DATE_STRIP_RE: re.Pattern = re.compile(r'[（(]\d{4}-\d{2}-\d{2}[）)]')

    @staticmethod
    def _classify_result_type(info: dict) -> str:
        """Return one of raw_information / analyses / feedbacks / nodes."""
        source = info.get("source")
        if isinstance(source, dict):
            if source.get("analysis_type"):
                return "analyses"
            if source.get("lessons_learned") is not None:
                return "feedbacks"
            if source.get("node_type") or source.get("ticker"):
                return "nodes"
            return "raw_information"
        row = info.get("row")
        if isinstance(row, dict):
            row_cls = row.get("__class_name__", "")
        elif row is not None:
            row_cls = type(row).__name__
        else:
            row_cls = ""
        if row_cls == "Analysis":
            return "analyses"
        if row_cls == "Feedback":
            return "feedbacks"
        if row_cls in ("NodeState", "WorldNode"):
            return "nodes"
        return "raw_information"

    @staticmethod
    def _dedup_by_title(
        bm25_results: dict[str, dict],
        pg_results: dict[str, dict],
        dropped: dict[str, int],
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        """Cluster raw_information / analyses / feedbacks by fuzzy title
        similarity within each type.  Nodes are never deduped.

        Within each cluster of near-identical titles, keep only the most
        recently published result.
        """
        DEDUP_ENTRY = tuple[str, str, str, dict]  # (pid, channel, rtype, info)

        all_entries: list[DEDUP_ENTRY] = []

        for pid, info in bm25_results.items():
            rtype = HardFilter._classify_result_type(info)
            if rtype != "nodes":
                all_entries.append((pid, "bm25", rtype, info))

        for pid, info in pg_results.items():
            rtype = HardFilter._classify_result_type(info)
            if rtype != "nodes":
                all_entries.append((pid, "pg", rtype, info))

        if len(all_entries) <= 1:
            return bm25_results, pg_results

        time_getter = HardFilter._extract_published_at_from_result

        entries_with_meta: list[tuple[str, str, str, dict, str, datetime | None]] = []
        for pid, channel, rtype, info in all_entries:
            title = HardFilter._extract_title_from_result(info)
            ts = time_getter(info)
            entries_with_meta.append((pid, channel, rtype, info, title, ts))

        # Cluster within each rtype independently — never cross-type.
        n = len(entries_with_meta)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(n):
            ri = entries_with_meta[i][2]
            ti = entries_with_meta[i][4]
            if not ti:
                continue
            for j in range(i + 1, n):
                rj = entries_with_meta[j][2]
                if ri != rj:
                    continue  # never cluster across different result types
                tj = entries_with_meta[j][4]
                if not tj:
                    continue
                threshold = HardFilter._DEDUP_THRESHOLDS.get(ri, 0.50)
                if HardFilter._bigram_jaccard(ti, tj) >= threshold:
                    union(i, j)

        clusters: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            clusters.setdefault(root, []).append(i)

        kept_indices: set[int] = set()
        total_dropped = 0
        now = datetime.now(timezone.utc)

        for root, indices in clusters.items():
            if len(indices) == 1:
                kept_indices.add(indices[0])
                continue

            def sort_key(i: int) -> tuple:
                ts = entries_with_meta[i][5]
                score = entries_with_meta[i][3].get("score", 0)
                return (ts is not None, ts or now.replace(year=1970), score)

            best_idx = max(indices, key=sort_key)
            kept_indices.add(best_idx)
            total_dropped += len(indices) - 1

        if total_dropped == 0:
            return bm25_results, pg_results

        kept_pids: dict[str, set[str]] = {"bm25": set(), "pg": set()}
        for i in kept_indices:
            pid, channel, _, _, _, _ = entries_with_meta[i]
            kept_pids[channel].add(pid)

        bm25_out = {}
        for pid, info in bm25_results.items():
            if HardFilter._classify_result_type(info) == "nodes" or pid in kept_pids["bm25"]:
                bm25_out[pid] = info

        pg_out = {}
        for pid, info in pg_results.items():
            if HardFilter._classify_result_type(info) == "nodes" or pid in kept_pids["pg"]:
                pg_out[pid] = info

        total_deduped = sum(len(v) - 1 for v in clusters.values() if len(v) > 1)
        dropped["title_dedup"] = total_deduped
        logger.info(
            "hard_filter=title_dedup clusters=%d dropped=%d kept=%d",
            len(clusters), total_deduped, len(kept_indices),
        )
        return bm25_out, pg_out

    @staticmethod
    def _extract_published_at_from_result(info: dict):
        """Extract published_at from a raw result dict (ES or PG)."""
        row = info.get("row")
        if isinstance(row, dict):
            pub = (row.get("published_at") or row.get("created_at")
                   or row.get("updated_at") or row.get("effective_from"))
            if pub:
                return pub
        elif row is not None:
            pub = (getattr(row, "published_at", None) or getattr(row, "created_at", None)
                   or getattr(row, "updated_at", None) or getattr(row, "effective_from", None))
            if pub:
                return pub
        source = info.get("source")
        if isinstance(source, dict):
            pub_str = source.get("published_at")
            if pub_str:
                try:
                    from dateutil.parser import parse as date_parse
                    return date_parse(pub_str)
                except Exception:
                    pass
        return None

    def _filter_low_vector(self, results: dict[str, dict]) -> tuple[dict[str, dict], int]:
        dropped = 0
        out: dict[str, dict] = {}
        for pid, info in results.items():
            score = info.get("score", 0)
            # Determine table from the row's class to select the right threshold.
            # Row may be an ORM object or a dict materialised by _pg_vector_search;
            # the dict carries __class_name__ for correct class detection.
            row = info.get("row")
            if row is not None:
                if isinstance(row, dict):
                    row_cls = row.get("__class_name__", "")
                else:
                    row_cls = type(row).__name__
                if row_cls == "Analysis":
                    tbl = "analyses"
                elif row_cls == "Feedback":
                    tbl = "feedbacks"
                elif row_cls in ("NodeState", "WorldNode"):
                    tbl = "nodes"
                else:
                    tbl = "raw_information"
                threshold = self.vector_thresholds.get(tbl, self.vector_low_threshold)
            else:
                threshold = self.vector_low_threshold
            if 0 < score < threshold:
                dropped += 1
                continue
            out[pid] = info
        return out, dropped

    @staticmethod
    def _filter_vector_by_date(
        results: dict[str, dict], date_range: dict,
    ) -> tuple[dict[str, dict], int]:
        dropped = 0
        out: dict[str, dict] = {}
        for pid, info in results.items():
            row = info.get("row")
            if isinstance(row, dict):
                pub = (row.get("published_at") or
                       row.get("created_at") or
                       row.get("updated_at"))
            elif row is not None:
                pub = (getattr(row, "published_at", None) or
                       getattr(row, "created_at", None) or
                       getattr(row, "updated_at", None))
            else:
                out[pid] = info
                continue
            if pub is not None:
                start = date_range.get("start")
                end = date_range.get("end")
                if start and pub < start:
                    dropped += 1
                    continue
                if end and pub > end:
                    dropped += 1
                    continue
            out[pid] = info
        return out, dropped

    @staticmethod
    def _drop_by_entities(
        results: dict[str, dict],
        ctx: SearchContext,
        dropped: dict[str, int],
    ) -> dict[str, dict]:
        """丢弃未命中任何实体的候选。

        候选文本必须包含至少一个实体的 name/ticker/alias。
        """
        out: dict[str, dict] = {}
        for pid, info in results.items():
            text = HardFilter._extract_text_from_result(info)
            if any(
                HardFilter._text_matches_entity(text, e)
                for e in ctx.entities
            ):
                out[pid] = info
            else:
                dropped["entity_absent"] = dropped.get("entity_absent", 0) + 1
        return out

    @staticmethod
    def _drop_inactive_nodes(
        results: dict[str, dict],
        dropped: dict[str, int],
    ) -> dict[str, dict]:
        """Drop node candidates whose WorldNode is inactive or missing.

        Node candidates reach the hard filter through three paths, each with
        different data available:

        1. ES ``quant_kb_nodes`` hit — pid is WorldNode UUID, source has
           ``is_active`` (True at index time, but sync may lag).
        2. ES ``quant_kb_node_states`` hit merged by node_id — pid is WorldNode
           UUID, source carries ``node_id`` but NO ``is_active``.
        3. PG vector ``_vector_search_nodes`` — pid is WorldNode UUID, row is
           enriched with WorldNode name/description.  If the WorldNode was
           not found (inactive/deleted), name/description stay absent.

        Strategy: for paths 1 & 2 (ES source present), we trust the source's
        ``is_active`` when available.  Otherwise we check whether PG row
        enrichment succeeded — a missing name + node_type means the WorldNode
        lookup failed, implying the node is gone.
        """
        out: dict[str, dict] = {}
        for pid, info in results.items():
            source = info.get("source")
            row = info.get("row")
            is_node = False
            is_active = True

            if isinstance(source, dict):
                if source.get("node_type") or source.get("ticker"):
                    # ES nodes index — pid IS the WorldNode UUID
                    is_node = True
                    is_active = source.get("is_active", True)
                elif source.get("node_id"):
                    # ES node_states index merged by node_id — pid IS the
                    # WorldNode UUID (remapped by _search_nodes_es).  The
                    # source has no is_active field, so we check the PG row
                    # enrichment (if available) or keep.
                    is_node = True
                    if row is not None:
                        if isinstance(row, dict):
                            if not row.get("name") and not row.get("node_type"):
                                is_active = False
                        elif type(row).__name__ == "WorldNode":
                            is_active = getattr(row, "is_active", True)

            if row is not None and not is_node:
                if isinstance(row, dict):
                    row_cls = row.get("__class_name__", "")
                    if row_cls in ("NodeState", "WorldNode"):
                        is_node = True
                        if not row.get("name") and not row.get("node_type"):
                            is_active = False
                elif type(row).__name__ in ("WorldNode", "NodeState"):
                    is_node = True
                    if type(row).__name__ == "WorldNode":
                        is_active = getattr(row, "is_active", True)
                    elif type(row).__name__ == "NodeState":
                        if not getattr(row, "name", None) and not getattr(row, "node_type", None):
                            is_active = False

            if is_node and not is_active:
                dropped["node_inactive"] = dropped.get("node_inactive", 0) + 1
                continue
            out[pid] = info

        return out

    # ── Entity detection helpers ─────────────────────────────────────

    @staticmethod
    def _text_matches_entity(text: str, entity: EntityResult) -> bool:
        if word_boundary_match(entity.name, text):
            return True
        if entity.ticker and word_boundary_match(entity.ticker, text):
            return True
        for alias in entity.aliases:
            if alias == "multi_stock":  # skip sentinel value
                continue
            if word_boundary_match(alias, text):
                return True
        return False
