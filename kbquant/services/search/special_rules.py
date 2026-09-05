"""阶段7.5: 特殊规则模块 — 可插拔的业务规则后处理。

每条规则是一个独立类，遵循统一协议:
- name: str — 规则标识
- applies_to: set[str] | None — 限定生效的意图，None 表示始终生效
- apply(self, candidates, ctx) -> list[Candidate]

通过 SpecialRules 构造函数的 rules 列表注册，按传入顺序执行。
"""
from __future__ import annotations

import inspect
import logging
import re
from datetime import datetime, timezone
from typing import Any

from kbquant.models.search_candidate import Candidate, SearchContext
from kbquant.utils.text import word_boundary_match

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """去空白、小写、移除常见后缀和日期，用于近似去重。"""
    t = title.strip().lower()
    # Strip parenthetical date suffixes: "（2026-04-15）" / "(2026-04-15)"
    t = _TITLE_DATE_STRIP_RE.sub('', t)
    for suffix in (
        " - 深度", " | 研报", " - 分析", "：深度解读",
        " - 深度分析", " | 深度", " - 专题", " | 专题",
        "（深度）", "(深度)", " - 点评", " | 点评",
        " - 快讯", " | 快讯",
    ):
        t = t.removesuffix(suffix.lower())
    return t.strip()


_TITLE_DATE_STRIP_RE = re.compile(r'[（(]\d{4}-\d{2}-\d{2}[）)]')


def _char_bigrams(text: str) -> set[str]:
    """Extract character-level bigrams for fuzzy title comparison.

    Parenthetical date suffixes like "（2026-04-15）" are stripped first
    so they don't artificially differentiate otherwise identical titles.
    """
    text = _TITLE_DATE_STRIP_RE.sub("", text).strip().lower()
    if len(text) < 2:
        return {text}
    return {text[i:i+2] for i in range(len(text) - 1)}


def _bigram_jaccard(a: str, b: str) -> float:
    """Jaccard similarity between character bigram sets of two strings."""
    ba = _char_bigrams(a)
    bb = _char_bigrams(b)
    if not ba or not bb:
        return 0.0
    intersection = len(ba & bb)
    union = len(ba | bb)
    return intersection / union if union > 0 else 0.0


# Per-result-type Jaccard threshold for fuzzy title dedup.
# raw_information: aggressive — flash quotes and volume alerts differ only
#   by a number (e.g. "成交额达100亿元" vs "成交额达200亿元").
# analysis: 0.50 — same-event rewrites typically score 0.53–0.60;
#   genuinely different analyses share < 0.50 even with the same subject.
# feedback/node: exact match only (they're small collections).
_FUZZY_DEDUP_THRESHOLDS: dict[str, float] = {
    "raw_information": 0.50,
    "analysis": 0.50,
    # feedback/node: never fuzzy-deduped (threshold 1.0 = exact only)
    "feedback": 1.0,
    "node": 1.0,
}


def _safety_floor(ctx: SearchContext) -> int:
    """安全底线：剩余结果数不少于 max(3, limit // 4)。"""
    return max(3, ctx.limit // 4)


def _filter_with_floor(
    candidates: list[Candidate],
    ctx: SearchContext,
    predicate,
) -> tuple[list[Candidate], int]:
    """Filter candidates, keeping floor items regardless of predicate."""
    floor = _safety_floor(ctx)
    kept: list[Candidate] = []
    dropped = 0
    for c in candidates:
        if predicate(c) or len(kept) < floor:
            kept.append(c)
        else:
            dropped += 1
    return kept, dropped


# ──────────────────────────────────────────────────────────────
# 规则1: 去重
# ──────────────────────────────────────────────────────────────

class DeduplicationRule:
    name = "dedup"
    applies_to: set[str] | None = None  # 始终生效，但 market_data 内部跳过模糊去重

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        intent = ctx.timings.get("dynamic_weights_intent", "general")

        dropped = 0
        seen_ids: dict[tuple[str, str], int] = {}
        seen_titles: dict[str, int] = {}
        fuzzy_groups: dict[str, list[tuple[str, int]]] = {
            "raw_information": [],
            "analysis": [],
        }

        result: list[Candidate] = []

        for c in candidates:
            # ── Exact ID dedup ──
            key = (c.table_name, c.id)
            if key in seen_ids:
                other_idx = seen_ids[key]
                if c.final_score > result[other_idx].final_score:
                    old_norm = _normalize_title(result[other_idx].title)
                    new_norm = _normalize_title(c.title)
                    fg = fuzzy_groups.get(c.result_type)
                    if fg is not None:
                        for fi, (ft, fi_idx) in enumerate(fg):
                            if fi_idx == other_idx:
                                fg[fi] = (new_norm, other_idx)
                                break
                    if old_norm in seen_titles and seen_titles[old_norm] == other_idx:
                        del seen_titles[old_norm]
                    result[other_idx] = c
                    if new_norm:
                        seen_titles[new_norm] = other_idx
                dropped += 1
                continue
            seen_ids[key] = len(result)

            # ── Exact title dedup (after normalize) ──
            norm = _normalize_title(c.title)
            if norm and norm in seen_titles:
                other_idx = seen_titles[norm]
                if c.final_score > result[other_idx].final_score:
                    result[other_idx] = c
                dropped += 1
                continue

            if norm:
                seen_titles[norm] = len(result)
                fg = fuzzy_groups.get(c.result_type)
                if fg is not None:
                    fg.append((norm, len(result)))

            result.append(c)

        # ── Fuzzy title dedup (skip for market_data intent) ──
        fuzzy_dropped = 0
        if intent != "market_data":
            indices_to_remove: set[int] = set()

            for rtype in ("raw_information", "analysis"):
                threshold = _FUZZY_DEDUP_THRESHOLDS[rtype]
                if threshold >= 1.0:
                    continue
                fg = fuzzy_groups[rtype]
                if len(fg) <= 1:
                    continue

                n = len(fg)
                # Pre-compute bigram sets once per title to avoid O(N²)
                # recomputation during pairwise comparison
                bigram_sets: list[set[str]] = [_char_bigrams(fg[i][0]) for i in range(n)]
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
                    bi = bigram_sets[i]
                    for j in range(i + 1, n):
                        # Fast path: compute Jaccard directly from pre-cached bigram sets
                        intersection = len(bi & bigram_sets[j])
                        union_size = len(bi | bigram_sets[j])
                        sim = intersection / union_size if union_size > 0 else 0.0
                        if sim >= threshold:
                            union(i, j)

                clusters: dict[int, list[int]] = {}
                for i in range(n):
                    root = find(i)
                    clusters.setdefault(root, []).append(i)

                for root, indices in clusters.items():
                    if len(indices) <= 1:
                        continue
                    best_i = max(indices, key=lambda i: result[fg[i][1]].final_score)
                    for i in indices:
                        if i != best_i:
                            indices_to_remove.add(fg[i][1])

            if indices_to_remove:
                result = [c for i, c in enumerate(result) if i not in indices_to_remove]
                fuzzy_dropped = len(indices_to_remove)

        total_dropped = dropped + fuzzy_dropped
        if total_dropped:
            logger.info(
                "special_rule=dedup exact_dropped=%d fuzzy_dropped=%d remaining=%d intent=%s",
                dropped, fuzzy_dropped, len(result), intent,
            )
        return result


# ──────────────────────────────────────────────────────────────
# 规则2: 最低分门槛
# ──────────────────────────────────────────────────────────────

class MinScoreThresholdRule:
    name = "min_score_threshold"
    applies_to: set[str] | None = None

    def __init__(self, threshold: float = 0.05):
        self.threshold = threshold

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        kept, dropped = _filter_with_floor(
            candidates, ctx,
            lambda c: c.final_score >= self.threshold,
        )
        if dropped:
            logger.info(
                "special_rule=min_score_threshold dropped=%d threshold=%.2f remaining=%d",
                dropped, self.threshold, len(kept),
            )
        return kept


# ──────────────────────────────────────────────────────────────
# 规则2.5: 过滤破损/空内容候选
# ──────────────────────────────────────────────────────────────

class EmptyContentFilterRule:
    name = "empty_content_filter"
    applies_to: set[str] | None = None  # 始终生效

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        before = len(candidates)
        filtered = [
            c for c in candidates
            if (c.title and c.title.strip()) or (c.snippet and c.snippet.strip())
        ]
        dropped = before - len(filtered)
        if dropped > 0:
            logger.warning(
                "special_rule=empty_content_filter dropped=%d remaining=%d",
                dropped, len(filtered),
            )
        return filtered


# ──────────────────────────────────────────────────────────────
# 规则3: 时效覆盖
# ──────────────────────────────────────────────────────────────

class FreshnessOverrideRule:
    name = "freshness_override"
    applies_to: set[str] | None = None  # self-gating: apply() checks intent + time_bias internally

    def __init__(self, news_max_age_days: int = 90, general_max_age_days: int = 365):
        self.news_max_age = news_max_age_days
        self.general_max_age = general_max_age_days

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        intent = ctx.timings.get("dynamic_weights_intent", "general")
        time_bias = ctx.time_bias_days

        if intent == "news":
            max_age_days = self.news_max_age
        elif time_bias is not None and time_bias > 0:
            max_age_days = self.general_max_age
        else:
            return candidates  # not applicable

        now = datetime.now(tz=timezone.utc)

        def _is_fresh(c: Candidate) -> bool:
            if c.time is None:
                return True
            ct = c.time if c.time.tzinfo else c.time.replace(tzinfo=timezone.utc)
            age_days = (now - ct).total_seconds() / 86400
            return age_days <= max_age_days

        kept, dropped = _filter_with_floor(candidates, ctx, _is_fresh)

        if dropped:
            logger.info(
                "special_rule=freshness_override dropped=%d max_age_days=%d remaining=%d",
                dropped, max_age_days, len(kept),
            )
        return kept


# ──────────────────────────────────────────────────────────────
# 规则4: 实体节点置顶
# ──────────────────────────────────────────────────────────────

class EntityWorldNodePinRule:
    name = "entity_node_pin"
    applies_to: set[str] | None = {"entity_lookup", "concept", "strategy"}

    def __init__(self, pin_position: int = 0, max_search_position: int = 10):
        """pin_position: 置顶到的位置（0 = 第1位）。"""
        self.pin_position = pin_position
        self.max_search_position = max_search_position

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        main_entity = ctx.main_entity
        if main_entity is None or not main_entity.node_id:
            return candidates

        target_node_id = main_entity.node_id
        for idx, c in enumerate(candidates):
            if idx > self.max_search_position:
                break
            if c.result_type == "node" and c.id == target_node_id:
                if idx == self.pin_position:
                    return candidates  # 已在目标位置
                # 移除后插入到目标位置
                node = candidates.pop(idx)
                candidates.insert(self.pin_position, node)
                logger.info(
                    "special_rule=entity_node_pin node_id=%s from=%d to=%d",
                    target_node_id, idx, self.pin_position,
                )
                return candidates

        return candidates


# ──────────────────────────────────────────────────────────────
# 规则5: 实体内容增强
# ──────────────────────────────────────────────────────────────

class EntityContentBoostRule:
    name = "entity_content_boost"
    applies_to: set[str] | None = {"entity_lookup"}

    def __init__(self, boost_multiplier: float = 1.1, top_n: int = 10):
        self.boost_multiplier = boost_multiplier
        self.top_n = top_n

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates or self.boost_multiplier == 1.0:
            return candidates

        main_entity = ctx.main_entity
        if main_entity is None or not main_entity.name:
            return candidates

        entity_name = main_entity.name.lower()
        boosted = 0

        for i, c in enumerate(candidates):
            if i >= self.top_n:
                break
            text = (c.title + " " + c.snippet).lower()
            if word_boundary_match(entity_name, text):
                c.final_score = round(c.final_score * self.boost_multiplier, 6)
                c.entity_boost = min(c.entity_boost + 0.1, 1.0)
                boosted += 1

        if boosted:
            logger.info(
                "special_rule=entity_content_boost boosted=%d entity=%s multiplier=%.2f",
                boosted, main_entity.name, self.boost_multiplier,
            )

        return candidates


# ──────────────────────────────────────────────────────────────
# 规则6: 概念节点注入
# ──────────────────────────────────────────────────────────────

class ConceptNodeInjectionRule:
    name = "concept_node_injection"
    applies_to: set[str] | None = {"concept"}

    def __init__(
        self,
        limit: int = 3,
        min_raw_count: int = 2,
    ):
        """limit: 最多注入几个节点；min_raw_count: raw_information 低于此数则不替换。"""
        self.limit = limit
        self.min_raw_count = min_raw_count

    async def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        # Reuse WorldNode data already fetched in stage 1 entity_resolver
        # (stored on ctx via ctx._resolved_worldnodes) instead of opening
        # a fresh DB session.
        resolved_nodes: list[dict] = getattr(ctx, "_resolved_worldnodes", None) or []
        if not resolved_nodes:
            return candidates

        # Extract keywords from expanded keywords
        expanded_keywords: set[str] = getattr(ctx, "expanded_keywords", None) or set()
        search_words: list[str] = sorted(
            [w for w in expanded_keywords if len(w) >= 2], key=len
        )[:5]
        if not search_words:
            return candidates

        matched_nodes: list[dict] = []
        seen_node_ids: set[str] = set()

        for rn in resolved_nodes:
            name = rn.get("name", "") or ""
            if any(w.lower() in name.lower() for w in search_words):
                nid = rn.get("id", "")
                if nid and nid not in seen_node_ids:
                    seen_node_ids.add(nid)
                    matched_nodes.append(rn)
            if len(matched_nodes) >= self.limit * 2:
                break

        if not matched_nodes:
            return candidates

        # 按名称长度与搜索词的匹配度排序（优先精确匹配，再按名称长度）
        query_lower = ctx.query_text.lower()
        scored: list[tuple[dict, float]] = []
        for node in matched_nodes:
            name_lower = (node.get("name", "") or "").lower()
            if name_lower == query_lower:
                score = 1.0
            elif name_lower in query_lower or query_lower in name_lower:
                score = 0.8
            else:
                score = 0.5
            scored.append((node, score))
        scored.sort(key=lambda x: -x[1])
        matched_nodes = [n for n, _ in scored[:self.limit]]

        # 检查哪些节点已经在候选项中
        existing_node_ids: set[str] = {
            c.id for c in candidates if c.result_type == "node"
        }

        # Compute median final_score for injected nodes
        if candidates:
            median_score = sorted(c.final_score for c in candidates)[len(candidates) // 2]
        else:
            median_score = 0.5

        injected = 0
        for node in matched_nodes:
            node_id = str(node.get("id", ""))
            if node_id in existing_node_ids:
                continue  # 已存在，跳过

            # 统计 raw_information 数量
            raw_indices = [
                i for i, c in enumerate(candidates)
                if c.result_type == "raw_information"
            ]
            raw_count = len(raw_indices)

            node_name = node.get("name", "") or ""
            node_desc = node.get("description", "") or ""
            node_time = node.get("updated_at")

            if raw_count >= self.min_raw_count + 2:
                # Sufficient raw_information: inject at median position
                # without removing any candidate
                median_pos = raw_indices[raw_count // 2]
                new_candidate = Candidate(
                    id=node_id,
                    table_name="nodes",
                    result_type="node",
                    title=node_name,
                    snippet=node_desc[:150],
                    time=node_time,
                    final_score=median_score,
                    entity_boost=0.5,
                    penalty_mult=1.0,
                    name_match_score=0.8,
                )
                candidates.insert(median_pos + 1, new_candidate)
                existing_node_ids.add(node_id)
                injected += 1
                logger.info(
                    "special_rule=concept_node_injection injected=%s at=%d",
                    node_name, median_pos + 1,
                )
            elif raw_count >= self.min_raw_count:
                # Only as last resort: replace the lowest final_score
                # raw_information, and only when the injected node would
                # genuinely improve results.
                worst_raw_idx = min(raw_indices, key=lambda i: candidates[i].final_score)
                if candidates[worst_raw_idx].final_score < median_score * 0.7:
                    removed = candidates.pop(worst_raw_idx)
                    new_candidate = Candidate(
                        id=node_id,
                        table_name="nodes",
                        result_type="node",
                        title=node_name,
                        snippet=node_desc[:150],
                        time=node_time,
                        final_score=median_score,
                        entity_boost=0.5,
                        penalty_mult=1.0,
                        name_match_score=0.8,
                    )
                    candidates.append(new_candidate)
                    existing_node_ids.add(node_id)
                    injected += 1
                    logger.info(
                        "special_rule=concept_node_injection injected=%s replaced=%s(%s)",
                        node_name, removed.title[:30], removed.id,
                    )

        if injected:
            logger.info(
                "special_rule=concept_node_injection total_injected=%d",
                injected,
            )

        return candidates


# ──────────────────────────────────────────────────────────────
# 规则7: 多样性注入
# ──────────────────────────────────────────────────────────────

class DiversityInjectionRule:
    name = "diversity_injection"
    applies_to: set[str] | None = {"general"}

    def __init__(
        self,
        window: int = 5,
        threshold: float = 0.7,
        inject_position: int = 3,
        max_injections: int = 2,
    ):
        self.window = window
        self.threshold = threshold
        self.inject_position = inject_position
        self.max_injections = max_injections

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if len(candidates) < self.window:
            return candidates

        injections = 0
        for _ in range(self.max_injections):
            if len(candidates) < self.window:
                break
            top_window = candidates[:self.window]
            type_counts: dict[str, int] = {}
            for c in top_window:
                type_counts[c.result_type] = type_counts.get(c.result_type, 0) + 1

            dominant_type = max(type_counts, key=lambda k: type_counts[k])
            dominant_ratio = type_counts[dominant_type] / self.window

            if dominant_ratio <= self.threshold:
                break

            # Find the next candidate of a different type to inject
            injected = False
            for i in range(self.window, len(candidates)):
                c = candidates[i]
                if c.result_type != dominant_type:
                    injection = candidates.pop(i)
                    target_pos = min(self.inject_position + injections, len(candidates))
                    candidates.insert(target_pos, injection)
                    injections += 1
                    logger.info(
                        "special_rule=diversity_injection injected_type=%s at=%d dominant=%s ratio=%.0f%%",
                        c.result_type, target_pos, dominant_type, dominant_ratio * 100,
                    )
                    injected = True
                    break
            if not injected:
                break

        return candidates


# ──────────────────────────────────────────────────────────────
# 规则8: 关键词重排序
# ──────────────────────────────────────────────────────────────

class SpecialKeywordRankingRule:
    name = "keyword_ranking"
    applies_to: set[str] | None = None  # 始终生效

    _KEYWORD_RULES: list[dict[str, Any]] = [
        {"keywords": ["财报", "季报", "年报"], "prefer_type": "analysis", "multiplier": 1.25},
        {"keywords": ["营收", "净利润", "ROE", "EPS"], "prefer_type": "analysis", "multiplier": 1.15},
        {"keywords": ["风险"], "prefer_types": ["analysis", "feedback"], "multiplier": 1.3},
        {"keywords": ["复盘"], "prefer_type": "feedback", "multiplier": 1.4},
        {"keywords": ["打板", "止损", "炸板", "龙虎榜"], "prefer_type": "feedback", "multiplier": 1.3},
        {"keywords": ["研报"], "prefer_type": "analysis", "multiplier": 1.6},
        {"keywords": ["知识图谱", "图谱"], "prefer_type": "node", "multiplier": 1.5},
        {"keywords": ["供应链", "产业链"], "prefer_table": "analyses", "multiplier": 1.2},
        # 扩充分析类查询的 analysis 类型增强
        {"keywords": ["深度分析", "深度研究", "深度解读"], "prefer_type": "analysis", "multiplier": 1.5},
        {"keywords": ["展望", "前景"], "prefer_type": "analysis", "multiplier": 1.4},
        {"keywords": ["分析"], "prefer_type": "analysis", "multiplier": 1.3,
         "exclude_keywords": ["涨停", "跌停", "反弹", "跳水", "炸板"]},
    ]

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        query_text = ctx.query_text
        if not query_text:
            return candidates

        # 找出匹配的规则
        matched_rules: list[dict[str, Any]] = []
        for rule in self._KEYWORD_RULES:
            # Check exclude keywords first — if any exclude matches the query
            # text, skip the entire rule regardless of keyword matches.
            excludes = rule.get("exclude_keywords", [])
            if excludes and any(exc in query_text for exc in excludes):
                continue
            for kw in rule["keywords"]:
                if kw in query_text:
                    matched_rules.append(rule)
                    break

        if not matched_rules:
            # 即使没有特定关键词匹配，若 intent 是 analysis 或 strategy
            # 仍然对 raw_information 做降权来减少资讯淹没分析报告
            intent = ctx.timings.get("dynamic_weights_intent", "general")
            if intent == "analysis":
                raw_demoted = 0
                for c in candidates:
                    if c.result_type == "raw_information":
                        c.final_score = round(c.final_score * 0.80, 6)
                        raw_demoted += 1
                if raw_demoted:
                    logger.info(
                        "special_rule=keyword_ranking intent=analysis raw_demoted=%d/%d",
                        raw_demoted, len(candidates),
                    )
            return candidates

        boosted_count = 0
        for rule in matched_rules:
            multiplier = rule["multiplier"]
            prefer_type = rule.get("prefer_type")
            prefer_types = rule.get("prefer_types")
            prefer_table = rule.get("prefer_table")

            for c in candidates:
                matched = False
                if prefer_type and c.result_type == prefer_type:
                    matched = True
                elif prefer_types and c.result_type in prefer_types:
                    matched = True
                elif prefer_table and c.table_name == prefer_table:
                    matched = True

                if matched:
                    c.final_score = round(c.final_score * multiplier, 6)
                    boosted_count += 1

        if boosted_count:
            keywords = [kw for r in matched_rules for kw in r["keywords"] if kw in query_text]
            logger.info(
                "special_rule=keyword_ranking triggered=%s boosted=%d",
                ",".join(keywords), boosted_count,
            )

        return candidates


# ──────────────────────────────────────────────────────────────
# 规则9: 节点优先 — 查询中包含 node/节点等词时，将所有 node 结果移到开头
# ──────────────────────────────────────────────────────────────

class NodePriorityRule:
    """当查询中包含 node、worldnode、节点、知识图谱等词时，
    将结果中所有 type=node 的候选项保持原有相对顺序移到列表开头。
    
    适用于用户明确想通过知识图谱探索的场景。
    """
    name = "node_priority"
    applies_to: set[str] | None = None  # 始终生效（内部自行判断是否触发）

    _TRIGGER_KEYWORDS: frozenset[str] = frozenset({
        "node", "nodes", "worldnode", "worldnodes",
        "world_node", "world node",
        "节点", "知识图谱", "图谱", "关系图谱",
        "知识节点", "实体节点", "实体关系",
    })

    def apply(
        self,
        candidates: list,
        ctx,
    ) -> list:
        if not candidates or len(candidates) <= 1:
            return candidates

        # Check if any trigger keyword appears in the query
        query_lower = ctx.query_text.lower()
        triggered = any(kw in query_lower for kw in self._TRIGGER_KEYWORDS)
        if not triggered:
            return candidates

        # Count nodes and non-nodes
        nodes: list = []
        others: list = []
        for c in candidates:
            if c.result_type == "node":
                nodes.append(c)
            else:
                others.append(c)

        if not nodes or not others:
            return candidates

        # Only boost nodes whose reranker_score crosses the noise threshold
        # (same _RERANKER_MIN_THRESHOLD = 0.01 used in FinalRanking).
        # Nodes with near-zero reranker scores are semantically irrelevant
        # to the query and should not be pushed above non-node results.
        # This is a principled filter: the reranker now has full node text
        # available (state_summary, core_logic, etc.), so a zero score
        # genuinely means the node is unrelated to the query topic.
        _RERANKER_MIN = 0.01
        relevant_nodes = [n for n in nodes if n.reranker_score >= _RERANKER_MIN]
        irrelevant_nodes = [n for n in nodes if n.reranker_score < _RERANKER_MIN]

        if not relevant_nodes:
            return candidates

        # Boost relevant node final_scores to be at least as high as the
        # highest non-node score, preserving relative node ordering.
        max_other_score = max(c.final_score for c in others)
        for node in relevant_nodes:
            if node.final_score < max_other_score:
                node.final_score = round(max_other_score + 0.001 * (node.final_score / max(max_other_score, 1)), 6)
            node.entity_boost = min(node.entity_boost + 0.2, 1.0)

        # Demote irrelevant nodes: replace their final_score with
        # their reranker_score directly.  This eliminats vector-rank
        # and RRF influence from the irrelevant tier entirely.
        # Nodes the reranker considers unrelated get a score equal to
        # their reranker_score, sorted purely by semantic relevance.
        for node in irrelevant_nodes:
            node.final_score = round(node.reranker_score, 6)

        logger.info(
            "special_rule=node_priority nodes_boosted=%d max_other=%.4f query_keywords=[%s]",
            len(nodes), max_other_score,
            ",".join(kw for kw in self._TRIGGER_KEYWORDS if kw in query_lower),
        )
        return candidates


class MarketRegionBoostRule:
    """根据地域意图对非目标市场内容软降权"""
    name = "market_region_boost"
    applies_to: set[str] | None = None  # 始终生效，不依赖 intent

    _REGION_INDICATORS = {
        "us": {"美联储", "Fed", "Federal Reserve", "纳斯达克", "Nasdaq", "纽交所", "NYSE",
               "标普", "S&P", "道琼斯", "Dow Jones", "美银", "BofA", "高盛", "Goldman",
               "摩根", "JPMorgan", "花旗", "Citigroup", "美国银行", "富国银行", "Wells Fargo"},
        "uk": {"英国央行", "BoE", "英格兰银行", "Bank of England", "英镑", "富时",
               "巴克莱", "Barclays", "汇丰", "HSBC"},
        "eu": {"欧洲央行", "ECB", "European Central Bank", "欧央行", "欧元区",
               "德银", "德意志", "法兴", "法巴", "BNP"},
        "jp": {"日本央行", "BoJ", "日经", "Nikkei", "野村", "Nomura", "大和"},
    }
    _CN_INDICATORS = {"中国", "央行", "A股", "沪市", "深市", "沪深", "人民币",
                      "创业板", "科创板", "北交所", "上证", "深证", "中证"}

    def __init__(self, demote_mult: float = 0.65):
        self.demote_mult = demote_mult

    def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        region = ctx.timings.get("target_region")
        if not region or region == "cn":
            return candidates

        confidence = ctx.timings.get("target_region_confidence", 0)
        if confidence < 0.5:
            return candidates

        indicators = self._REGION_INDICATORS.get(region, set())
        demoted = 0
        floor = _safety_floor(ctx)

        for i, c in enumerate(candidates):
            if i < floor:
                continue
            text = (c.title + " " + (c.snippet or "")).lower()
            if any(ind.lower() in text for ind in indicators):
                continue
            if region != "cn" and any(ind in text for ind in self._CN_INDICATORS):
                continue
            c.final_score = round(c.final_score * self.demote_mult, 6)
            demoted += 1

        if demoted:
            logger.info(
                "special_rule=market_region region=%s confidence=%.2f demoted=%d/%d",
                region, confidence, demoted, len(candidates),
            )
        return candidates


# ──────────────────────────────────────────────────────────────
# 编排器
# ──────────────────────────────────────────────────────────────

class SpecialRules:
    """阶段7.5: 特殊规则编排器。

    通过构造函数传入规则列表，按顺序执行。
    每条规则检查 applies_to 决定是否对当前意图生效。
    """

    def __init__(self, rules: list | None = None):
        self._rules: list = rules or _default_rules()

    async def apply(
        self,
        candidates: list[Candidate],
        ctx: SearchContext,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        intent = ctx.timings.get("dynamic_weights_intent", "general")
        result = candidates
        total_run = 0

        for rule in self._rules:
            if rule.applies_to is not None and intent not in rule.applies_to:
                continue

            before_count = len(result)
            before_types = self._top_type_dist(result)

            if inspect.iscoroutinefunction(rule.apply):
                # async apply（ConceptNodeInjectionRule）
                result = await rule.apply(result, ctx)
            else:
                result = rule.apply(result, ctx)

            after_count = len(result)
            after_types = self._top_type_dist(result)
            total_run += 1

            if before_count != after_count:
                logger.info(
                    "special_rule=%s dropped=%d remaining=%d",
                    rule.name, before_count - after_count, after_count,
                )
            if before_types != after_types:
                logger.debug(
                    "special_rule=%s type_shift: %s -> %s",
                    rule.name, before_types, after_types,
                )

        ctx.timings["special_rules_count"] = total_run
        # Single final sort after ALL rules have run
        result.sort(key=lambda c: c.final_score, reverse=True)
        return result

    @staticmethod
    def _top_type_dist(candidates: list[Candidate], n: int = 5) -> str:
        """前N候选的类型分布，用于日志对比。"""
        types = [c.result_type for c in candidates[:n]]
        return "|".join(types)


def _default_rules() -> list:
    return [
        MinScoreThresholdRule(threshold=0.05),
        FreshnessOverrideRule(),
        NodePriorityRule(),
        EntityWorldNodePinRule(),
        EntityContentBoostRule(),
        DiversityInjectionRule(),
        SpecialKeywordRankingRule(),
        DeduplicationRule(),  # runs last: after all insertions/modifications
    ]


# Module-level singleton — created once, reused across all search requests.

_DEFAULT_RULES_INSTANCE: list | None = None


def _get_default_rules() -> list:
    global _DEFAULT_RULES_INSTANCE
    if _DEFAULT_RULES_INSTANCE is None:
        from kbquant.config import settings
        ff = settings.feature_flags
        rules: list = [
            MinScoreThresholdRule(threshold=0.05),
            FreshnessOverrideRule(news_max_age_days=90),
            NodePriorityRule(),
            EntityWorldNodePinRule(),
            EntityContentBoostRule(),
            ConceptNodeInjectionRule(limit=3),
            DiversityInjectionRule(threshold=0.8),
            SpecialKeywordRankingRule(),
            DeduplicationRule(),
        ]
        if ff.get("empty_content_filter", True):
            rules.insert(1, EmptyContentFilterRule())
        if ff.get("market_region_boost", True):
            rules.append(MarketRegionBoostRule())
        _DEFAULT_RULES_INSTANCE = rules
    return _DEFAULT_RULES_INSTANCE
