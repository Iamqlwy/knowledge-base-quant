"""阶段5: RRF 融合 - 多通道 Reciprocal Rank Fusion。

融合 BM25、向量、名称匹配三个通道的排序结果，
加入结构重要性、时间衰减和 entity_context 加权。
"""
import logging
import math
import re
from datetime import datetime, timezone

try:
    from dateutil.parser import parse as date_parse
except ImportError:
    date_parse = None  # type: ignore[assignment]
from typing import Any

from kbquant.models.search_candidate import Candidate, SearchContext
from kbquant.utils.text import word_boundary_match

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = {
    "bm25": 1.2,
    "vector": 1.0,
    "name_match": 2.5,
    "structural": 0.2,
    "time_decay": 0.25,
    "position": 0.12,
}


class FusionService:
    """阶段5: RRF (Reciprocal Rank Fusion) 多通道融合。"""

    def __init__(
        self,
        rrf_k: int = 60,
        time_lambda: float = 0.05,
        weights: dict | None = None,
    ):
        self.rrf_k = rrf_k
        self.time_lambda = time_lambda
        self.weights = {**_DEFAULT_WEIGHTS, **(weights or {})}

    def fuse(
        self,
        bm25_results: dict[str, dict],
        vector_results: dict[str, dict],
        name_match_results: dict[str, dict],
        ctx: SearchContext | None = None,
    ) -> list[Candidate]:
        """Execute RRF fusion, returning a sorted candidate list."""
        w = self.weights
        # ── Adaptive RRF k (global) ──
        # Scale global k by number of active channels so single-channel-only
        # scenarios (e.g. bm25 mode) don't drown candidates in a large denominator.
        # Per-candidate k is always the global k — multi-channel candidates get
        # strictly higher RRF because they contribute more terms.
        active_channels = 1 + bool(vector_results) + bool(name_match_results)
        k = self.rrf_k * active_channels // 3  # 20 / 40 / 60

        # Rank computation
        bm25_ranked = sorted(bm25_results.items(), key=lambda x: x[1]["score"], reverse=True)
        vec_ranked = sorted(vector_results.items(), key=lambda x: x[1]["score"], reverse=True)

        bm25_ranks: dict[str, int] = {}
        for i, (pid, _) in enumerate(bm25_ranked):
            bm25_ranks[pid] = i + 1

        vec_ranks: dict[str, int] = {}
        for i, (pid, _) in enumerate(vec_ranked):
            vec_ranks[pid] = i + 1

        name_ranks: dict[str, int] = {}
        if name_match_results:
            sorted_names = sorted(
                name_match_results.items(), key=lambda x: x[1]["score"], reverse=True
            )
            for i, (pid, _) in enumerate(sorted_names):
                name_ranks[pid] = i + 1

        now = datetime.now(timezone.utc)
        now_naive = now.replace(tzinfo=None)
        all_ids = bm25_ranks.keys() | vec_ranks.keys() | name_ranks.keys()

        candidates: list[Candidate] = []
        for pid in all_ids:
            rrf = 0.0
            has_bm25 = pid in bm25_ranks
            has_vec = pid in vec_ranks
            has_name = pid in name_ranks
            if has_bm25:
                rrf += w["bm25"] / (k + bm25_ranks[pid])
            if has_vec:
                rrf += w["vector"] / (k + vec_ranks[pid])
            if has_name:
                rrf += w["name_match"] / (k + name_ranks[pid])

            pg_hit = vector_results.get(pid)
            es_hit = bm25_results.get(pid)
            nm_hit = name_match_results.get(pid)
            row = pg_hit.get("row") if pg_hit else None
            if row is None:
                importance_raw = 0.0
            else:
                importance_raw = float(row.get("importance_score", 0.0) or 0.0)
            importance_norm = min(max(importance_raw, 0.0), 1.0)
            rrf += w["structural"] * importance_norm / (k + 1)

            # Time decay: sigmoid-based — sharper discrimination in 0–90 day range.
            # Fresh docs (0 days) → ~1.0, 30 days → ~0.50, 90 days → ~0.12.
            time_score = 1.0
            published_at = self._extract_published_at(row, es_hit)
            if published_at:
                if hasattr(published_at, "tzinfo") and published_at.tzinfo is not None:
                    published_at = published_at.astimezone(timezone.utc).replace(tzinfo=None)
                days = max(0, (now_naive - published_at).days)
                c = self.time_lambda * days
                time_score = 2.0 / (1.0 + math.exp(c / 10.0))
                # sigmoid 2/(1+exp(c/10)): fresh (0d)→1.0, 30d→0.93, 90d→0.78
            rrf *= 1.0 + w["time_decay"] * time_score

            # Position decay: terms appearing earlier in body text → higher score.
            # Uses 1/(1 + pos/100) per term, averaged across all query terms.
            position_score = self._compute_position_score(
                ctx.query_text if ctx else "", es_hit, pg_hit,
            )
            rrf *= 1.0 + w.get("position", 0.12) * position_score

            # Entity context boost: computed here but applied in FinalRanking
            # (not multiplied onto RRF) to avoid double-counting the same signal.
            entity_boost = 0.0
            if ctx and ctx.entity_context:
                entity_boost = self._compute_entity_context_boost(
                    es_hit, pg_hit, ctx.entity_context
                )

            result_type, table_name = self._determine_type(es_hit, pg_hit, nm_hit)

            # Per-table weight boost (intent-driven)
            table_weights = w.get("table_weights", {})
            if table_weights and table_name in table_weights:
                rrf *= table_weights[table_name]

            candidates.append(Candidate(
                id=pid,
                table_name=table_name,
                result_type=result_type,
                title=self._extract_title(es_hit, pg_hit, nm_hit),
                snippet="",  # filled later by SnippetService
                time=published_at,
                bm25_score=float(bm25_results.get(pid, {}).get("score", 0)),
                vector_score=float(vector_results.get(pid, {}).get("score", 0)),
                name_match_score=float(nm_hit.get("score", 0) if nm_hit else 0),
                rrf_score=round(rrf, 6),
                importance_score=round(importance_norm, 4),
                time_score=round(time_score, 4),
                entity_boost=round(entity_boost, 4),
                bm25_rank=bm25_ranks.get(pid),
                vector_rank=vec_ranks.get(pid),
                name_match_rank=name_ranks.get(pid),
                pg_id=pid,
                raw=row,
                es_source=es_hit.get("source", {}) if es_hit else {},
                score_breakdown={
                    "rrf_raw": round(rrf, 6),
                    "structural_contrib": round(w["structural"] * importance_norm / (k + 1), 6),
                    "time_contrib": round(time_score, 4),
                    "position_contrib": round(position_score, 4),
                    "entity_boost_computed": round(entity_boost, 4),
                    "active_channels": active_channels,
                },
            ))

        candidates.sort(key=lambda c: c.rrf_score, reverse=True)
        return candidates

    @staticmethod
    def _extract_published_at(row, es_hit) -> Any:
        if row is not None:
            pub = (
                row.get("published_at")
                or row.get("created_at")
                or row.get("updated_at")
                or row.get("effective_from")
            )
            if pub:
                return pub
        if es_hit:
            src = es_hit.get("source", {})
            if src.get("published_at"):
                try:
                    if date_parse is not None:
                        return date_parse(src["published_at"])
                except Exception:
                    pass
        return None

    @staticmethod
    def _compute_entity_context_boost(
        es_hit, pg_hit, entity_context: dict[str, float]
    ) -> float:
        """Check if document mentions entity_context entities, weight by strength.

        Searches across all available text fields per table:
        - raw_information: title, body
        - analyses: title, content
        - feedbacks: title, lessons_learned
        - nodes: name, description, node_type (from ES source) or description (from PG)
        """
        parts: list[str] = []
        if es_hit:
            src = es_hit.get("source", {})
            parts.append(src.get("title", "") or "")
            parts.append(src.get("body", "") or "")
            parts.append(src.get("content", "") or "")
            parts.append(src.get("lessons_learned", "") or "")
            parts.append(src.get("description", "") or "")
        if pg_hit and pg_hit.get("row"):
            row = pg_hit["row"]
            parts.append(row.get("title", "") or "")
            parts.append(row.get("body", "") or "")
            parts.append(row.get("content", "") or "")
            parts.append(row.get("lessons_learned", "") or "")
            parts.append(row.get("description", "") or "")

        combined = " ".join(p for p in parts if p).lower()
        if not combined:
            return 0.0

        total_boost = 0.0
        for ctx_name, ctx_strength in entity_context.items():
            if word_boundary_match(ctx_name, combined):
                total_boost += ctx_strength

        return min(total_boost, 1.0)

    _WB_POSITION_CACHE: dict[str, re.Pattern] = {}

    @classmethod
    def _find_term_position(cls, term: str, text: str) -> int:
        if term.isascii():
            cache = cls._WB_POSITION_CACHE
            pattern = cache.get(term)
            if pattern is None:
                name = re.escape(term)
                pattern = re.compile(r"(?<![a-zA-Z0-9])" + name + r"(?![a-zA-Z0-9])")
                if len(cache) > 4096:
                    cache.pop(next(iter(cache)))
                cache[term] = pattern
            if m := pattern.search(text):
                return m.start()
            return -1
        return text.find(term)

    @staticmethod
    def _compute_position_score(
        query_text: str,
        es_hit: dict | None,
        pg_hit: dict | None,
    ) -> float:
        """Compute position-based decay: terms earlier in text → higher score.

        Splits query into terms (>= 2 chars), finds the earliest character
        position of each term in the combined body/content text, and applies
        the decay function 1/(1 + pos/100).

        - pos 0   → 1.0
        - pos 100 → 0.5
        - pos 500 → 0.17
        - pos 1000 → 0.09

        Returns the average across all terms.  When no body text is available
        (e.g. node-only results), returns 0.5 (neutral midpoint), with a log-length penalty on long body text.
        """
        # Collect body text from ES source and PG row
        parts: list[str] = []
        es_src = es_hit.get("source", es_hit) if es_hit else None
        if isinstance(es_src, dict):
            parts.append(es_src.get("body", "") or "")
            parts.append(es_src.get("content", "") or "")
            parts.append(es_src.get("lessons_learned", "") or "")
            parts.append(es_src.get("description", "") or "")
        pg_row = pg_hit.get("row") if pg_hit else None
        if pg_row and isinstance(pg_row, dict):
            parts.append(pg_row.get("body", "") or "")
            parts.append(pg_row.get("content", "") or "")
            parts.append(pg_row.get("lessons_learned", "") or "")
            parts.append(pg_row.get("description", "") or "")

        combined = " ".join(p for p in parts if p).lower()
        if not combined:
            return 0.5

        terms = [t.lower() for t in query_text.split() if len(t) >= 2]
        if not terms:
            return 1.0

        scores: list[float] = []
        for term in terms:
            pos = FusionService._find_term_position(term, combined)
            if pos == -1:
                # Query term not found in body → penalize
                scores.append(0.0)
            else:
                scores.append(1.0 / (1.0 + pos / 100.0))

        avg_score = sum(scores) / len(scores)
        # Body-length penalty: position in long texts is less meaningful.
        # 500 chars → 1.0, 2000 chars → ~0.83, 5000 chars → ~0.75, 10000 chars → ~0.70
        _len_ref = 500.0
        if combined and len(combined) > _len_ref:
            length_penalty = 1.0 / (1.0 + 0.1 * math.log2(len(combined) / _len_ref))
            return avg_score * length_penalty
        return avg_score

    @staticmethod
    def _determine_type(es_hit, pg_hit, nm_hit=None) -> tuple[str, str]:
        if es_hit:
            src = es_hit.get("source", {})
            if src.get("analysis_type"):
                return "analysis", "analyses"
            if src.get("lessons_learned") is not None:
                return "feedback", "feedbacks"
            if src.get("node_type") or src.get("ticker"):
                return "node", "nodes"
            if src.get("node_id"):
                return "node", "nodes"
            return "raw_information", "raw_information"
        if pg_hit and pg_hit.get("row"):
            row = pg_hit["row"]
            model_name = row.get("__class_name__", "")
            if model_name == "Analysis":
                return "analysis", "analyses"
            if model_name == "Feedback":
                return "feedback", "feedbacks"
            if model_name in ("WorldNode", "NodeState"):
                return "node", "nodes"
        if nm_hit:
            entity = nm_hit.get("entity")
            if entity is not None:
                return "node", "nodes"
            if nm_hit.get("row") is not None:
                return "node", "nodes"
            if nm_hit.get("source") == "direct_match":
                return "node", "nodes"
        return "raw_information", "raw_information"

    @staticmethod
    def _extract_title(
        es_hit: dict | None,
        pg_hit: dict | None,
        nm_hit: dict | None = None,
    ) -> str:
        if es_hit:
            src = es_hit.get("source", {})
            t = src.get("title", "") or src.get("name", "") or src.get("state_summary", "")
            if t:
                return t
        if pg_hit and pg_hit.get("row"):
            row = pg_hit["row"]
            t = row.get("title", "") or row.get("name", "") or row.get("state_summary", "")
            if t:
                return t
        if nm_hit:
            entity = nm_hit.get("entity")
            if entity:
                return getattr(entity, "name", "") or ""
            row = nm_hit.get("row")
            if row is not None:
                name = getattr(row, "name", None) or (row.get("name") if isinstance(row, dict) else None)
                if name:
                    return str(name)
        return ""
