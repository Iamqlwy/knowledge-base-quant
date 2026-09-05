"""阶段7: 最终排序 - 两套权重 (normal/fallback) + time_freshness + per-entity-type priorities.

在 RRF + Rerank 之后做最终排序:
- normal: reranker_score 可用时, reranker 占主导
- fallback: reranker 不可用时, 使用 rrf_score
- 统一叠加 entity_boost, time_freshness 和 type_priority
"""
import logging

from kbquant.models.search_candidate import Candidate, RankedItem, SearchContext
from kbquant.utils.text import word_boundary_match

logger = logging.getLogger(__name__)

# Reranker scores below this threshold are treated as noise — the candidate
# is demoted to fallback weights so that near-zero reranker scores don't
# ride on RRF+time+type components into the top results.
_RERANKER_MIN_THRESHOLD = 0.01

_NORMAL_WEIGHTS = {
    "alpha": 0.50,  # reranker
    "beta": 0.20,   # rrf
    "gamma": 0.10,  # entity_boost
    "delta": 0.10,  # time_freshness
    "epsilon": 0.10, # type_priority
}

_FALLBACK_WEIGHTS = {
    "alpha": 0.0,   # reranker (disabled)
    "beta": 0.4,    # rrf
    "gamma": 0.15,  # entity_boost
    "delta": 0.15,  # time_freshness
    "epsilon": 0.33, # type_priority — higher weight to counter analysis bias
}

# Intent → (alpha, beta, gamma, delta, epsilon) for normal mode
# Different intents have different "best signals":
#   concept: RRF+type_priority dominate, reranker minimized (nodes/KG)
#   entity_lookup: entity_boost+type_priority dominate (exact match)
#   news: time_freshness dominates (recency)
#   analysis: reranker dominates (semantic depth)
#   strategy: balanced with type_priority tilt toward feedback
#   general: balanced fallback
_INTENT_NORMAL_WEIGHTS: dict[str, dict[str, float]] = {
    "entity_lookup": {"alpha": 0.20, "beta": 0.25, "gamma": 0.25, "delta": 0.05, "epsilon": 0.25},
    "news":          {"alpha": 0.25, "beta": 0.20, "gamma": 0.08, "delta": 0.35, "epsilon": 0.12},
    "analysis":      {"alpha": 0.50, "beta": 0.20, "gamma": 0.10, "delta": 0.10, "epsilon": 0.10},
    "strategy":      {"alpha": 0.35, "beta": 0.25, "gamma": 0.08, "delta": 0.12, "epsilon": 0.20},
    "concept":       {"alpha": 0.75, "beta": 0.05, "gamma": 0.05, "delta": 0.05, "epsilon": 0.10},
    "market_data":   {"alpha": 0.20, "beta": 0.20, "gamma": 0.05, "delta": 0.40, "epsilon": 0.15},
    "general":       {"alpha": 0.40, "beta": 0.25, "gamma": 0.12, "delta": 0.10, "epsilon": 0.13},
}

# Intent → (alpha, beta, gamma, delta, epsilon) for fallback mode (no reranker)
_INTENT_FALLBACK_WEIGHTS: dict[str, dict[str, float]] = {
    "entity_lookup": {"alpha": 0.00, "beta": 0.35, "gamma": 0.30, "delta": 0.05, "epsilon": 0.30},
    "news":          {"alpha": 0.00, "beta": 0.30, "gamma": 0.08, "delta": 0.45, "epsilon": 0.17},
    "analysis":      {"alpha": 0.00, "beta": 0.50, "gamma": 0.20, "delta": 0.10, "epsilon": 0.20},
    "strategy":      {"alpha": 0.00, "beta": 0.33, "gamma": 0.10, "delta": 0.15, "epsilon": 0.42},
    "concept":       {"alpha": 0.00, "beta": 0.45, "gamma": 0.20, "delta": 0.10, "epsilon": 0.25},
    "market_data":   {"alpha": 0.00, "beta": 0.30, "gamma": 0.05, "delta": 0.50, "epsilon": 0.15},
    "general":       {"alpha": 0.00, "beta": 0.40, "gamma": 0.15, "delta": 0.12, "epsilon": 0.33},
}

# Intent → result-type priorities
# Different intents prefer different result types:
#   entity_lookup: raw_information first (facts about an entity)
#   news: raw_information first (timely updates)
#   analysis: analysis first (deep semantic content)
#   strategy: feedback first (strategic advice / market sentiment)
#   concept: analysis + node first (KG / knowledge exploration)
#   general: balanced raw_information-first fallback
_INTENT_TYPE_PRIORITY: dict[str, dict[str, float]] = {
    "entity_lookup": {
        "raw_information": 1.0,
        "node": 0.85,
        "analysis": 0.75,
        "feedback": 0.5,
    },
    "news": {
        "raw_information": 2.0,
        "analysis": 0.3,
        "node": 0.3,
        "feedback": 0.3,
    },
    "analysis": {
        "analysis": 1.10,
        "raw_information": 0.75,
        "feedback": 0.8,
        "node": 0.9,
    },
    "strategy": {
        "feedback": 1.5,
        "analysis": 0.75,
        "raw_information": 0.78,
        "node": 0.6,
    },
    "concept": {
        "analysis": 0.85,
        "node": 1.0,
        "raw_information": 0.75,
        "feedback": 0.6,
    },
    "market_data": {
        "raw_information": 2.0,
        "analysis": 0.35,
        "node": 0.35,
        "feedback": 0.25,
    },
    "general": {
        "raw_information": 1.0,
        "analysis": 0.85,
        "node": 0.7,
        "feedback": 0.6,
    },
}


class FinalRanking:
    """阶段7: 最终排序。

    两套权重:
    - normal: reranker 可用时
    - fallback: reranker 不可用时 (降级为 RRF-only)
    """

    def __init__(
        self,
        normal_weights: dict | None = None,
        fallback_weights: dict | None = None,
        normal_weights_by_intent: dict | None = None,
        fallback_weights_by_intent: dict | None = None,
    ):
        self._explicit_normal = normal_weights
        self._explicit_fallback = fallback_weights
        self.normal_weights_by_intent = normal_weights_by_intent or _INTENT_NORMAL_WEIGHTS
        self.fallback_weights_by_intent = fallback_weights_by_intent or _INTENT_FALLBACK_WEIGHTS

    def rank(
        self,
        candidates: list[Candidate],
        ctx: SearchContext | None = None,
        reranker_threshold_filter: bool = True,
        analysis_query_type_boost: bool = True,
    ) -> list[Candidate]:
        if not candidates:
            return candidates

        # Per-candidate reranker validity: a candidate only benefits from
        # normal (reranker-heavy) weights when its reranker_score exceeds the
        # minimum threshold.  Candidates with near-zero reranker scores
        # (0.0–0.0007 in observed noise) use fallback weights, which gives
        # more weight to RRF and type_priority and less to the near-zero
        # reranker component.
        if reranker_threshold_filter:
            has_any_valid_reranker = any(
                c.reranker_score > _RERANKER_MIN_THRESHOLD for c in candidates
            )
        else:
            has_any_valid_reranker = True

        # --- Intent-based weight selection ---
        # Priority: explicit overrides > intent-based lookup > hardcoded defaults
        intent = "general"
        if ctx and ctx.timings.get("dynamic_weights_intent"):
            intent = ctx.timings["dynamic_weights_intent"]

        # Pre-resolve both weight sets (they're the same for all candidates
        # with the same validity).
        if has_any_valid_reranker:
            if self._explicit_normal is not None:
                normal_weights = self._explicit_normal
            else:
                normal_weights = self.normal_weights_by_intent.get(intent, _NORMAL_WEIGHTS)
            if self._explicit_fallback is not None:
                fallback_weights = self._explicit_fallback
            else:
                fallback_weights = self.fallback_weights_by_intent.get(intent, _FALLBACK_WEIGHTS)
        else:
            normal_weights = _NORMAL_WEIGHTS  # unused but prevents unbound warning
            if self._explicit_fallback is not None:
                fallback_weights = self._explicit_fallback
            else:
                fallback_weights = self.fallback_weights_by_intent.get(intent, _FALLBACK_WEIGHTS)

        priority_map = _INTENT_TYPE_PRIORITY.get(intent, _INTENT_TYPE_PRIORITY["general"])

        # Min-max normalization into [0, 1].
        rrf_scores = [c.rrf_score for c in candidates]
        rrf_min = min(rrf_scores)
        rrf_max = max(rrf_scores)
        rrf_range = rrf_max - rrf_min

        if has_any_valid_reranker:
            reranker_scores = [c.reranker_score for c in candidates]
            reranker_min = min(reranker_scores)
            reranker_max = max(reranker_scores)
            reranker_range = reranker_max - reranker_min
        else:
            reranker_min = 0.0
            reranker_range = 1.0

        for c in candidates:
            # Use min-max normalization when range is non-trivial (>1e-6),
            # otherwise all candidates get equal contribution (0.5).
            if rrf_range > 1e-6:
                norm_rrf = (c.rrf_score - rrf_min) / rrf_range
            else:
                norm_rrf = 0.5

            has_reranker = (
                has_any_valid_reranker and c.reranker_score > _RERANKER_MIN_THRESHOLD if reranker_threshold_filter else True
            )
            if has_reranker and reranker_range > 1e-6:
                norm_reranker = (c.reranker_score - reranker_min) / reranker_range
            elif has_reranker:
                # All reranker scores are nearly identical — reranker didn't
                # discriminate.  Fall back to rrf_score in the reranker slot
                # instead of zeroing out the alpha weight.
                if rrf_range > 1e-6:
                    norm_reranker = (c.rrf_score - rrf_min) / rrf_range
                else:
                    norm_reranker = 0.5
            else:
                norm_reranker = 0.0

            # Per-candidate weight selection
            w = normal_weights if has_reranker else fallback_weights

            # Entity boost: when rrf_score > 0 the fusion stage has already
            # set entity_boost on the candidate (including any HardFilter
            # demotions).  When fusion didn't run (unit-test / synthetic path)
            # compute from title + ctx.
            entity_boost = c.entity_boost if c.entity_boost > 0 else self._compute_entity_boost(c.title, ctx)
            # Apply hard_filter penalties (keyword coverage, non-stock demotion)
            # penalty_mult is 1.0 when no penalties fired; range [0.0, 1.0]
            entity_boost *= c.penalty_mult

            type_pri = priority_map.get(c.result_type, 0.6) if analysis_query_type_boost else 1.0

            time_fresh = c.time_score

            final = (
                w["alpha"] * norm_reranker
                + w["beta"] * norm_rrf
                + w["gamma"] * entity_boost
                + w["delta"] * time_fresh
                + w["epsilon"] * type_pri
            )

            c.final_score = round(final, 6)

            c.score_breakdown["final"] = {
                "weights_used": f"{intent}_{'normal' if has_reranker else 'fallback'}",
                "alpha_term": round(w["alpha"] * norm_reranker, 6),
                "beta_term": round(w["beta"] * norm_rrf, 6),
                "gamma_term": round(w["gamma"] * entity_boost, 6),
                "delta_term": round(w["delta"] * time_fresh, 6),
                "epsilon_term": round(w["epsilon"] * type_pri, 6),
                "has_reranker": has_reranker,
            }

        candidates.sort(key=lambda c: c.final_score, reverse=True)

        if ctx is not None:
            ctx.timings["final_ranking_weights"] = f"{intent}_normal" if has_any_valid_reranker else f"{intent}_fallback"

        return candidates

    @staticmethod
    def _compute_entity_boost(title: str, ctx: SearchContext | None) -> float:
        """Compute entity match boost using word-boundary-aware matching,
        checking ALL resolved entities (not just main_entity)."""
        if not ctx or not ctx.entities:
            return 0.0

        title_lower = title.lower()
        boost = 0.0

        for entity in ctx.entities:
            name = entity.name or ""
            ticker = entity.ticker or ""

            if ticker and word_boundary_match(ticker, title_lower):
                boost += 0.2
                continue  # don't double-count name for the same entity
            if name and word_boundary_match(name, title_lower):
                boost += 0.15
                continue
            if any(word_boundary_match(a, title_lower) for a in (entity.aliases or [])):
                boost += 0.08

        return min(boost, 1.0)

    def to_ranked_items(
        self,
        candidates: list[Candidate],
    ) -> list[RankedItem]:
        """将 Candidate 列表转换为 API 响应格式的 RankedItem 列表。"""
        items = []
        for c in candidates:
            items.append(RankedItem(
                result_type=c.result_type,
                id=c.id,
                title=c.title,
                snippet=c.snippet,
                time=c.time,
                score={
                    "total": c.final_score,
                    "bm25_rank": c.bm25_rank,
                    "vector_rank": c.vector_rank,
                    "structural": c.importance_score,
                    "time_score": c.time_score,
                    "reranker": c.reranker_score if c.reranker_score > 0 else None,
                    "breakdown": c.score_breakdown,
                },
            ))
        return items
