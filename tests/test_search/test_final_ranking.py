"""Unit tests for final_ranking — stage 7."""
import pytest

from kbquant.services.search.final_ranking import FinalRanking
from kbquant.models.search_candidate import Candidate, SearchContext, EntityResult


class TestFinalRanking:
    def setup_method(self):
        self.ranking = FinalRanking()

    def _make_candidate(self, id="1", title="test", result_type="raw_information",
                        rrf_score=0.05, reranker_score=0.0, entity_boost=0.0):
        return Candidate(
            id=id, table_name="raw_information", result_type=result_type,
            title=title, rrf_score=rrf_score, reranker_score=reranker_score,
            entity_boost=entity_boost, bm25_score=1.0,
        )

    def test_rank_empty(self):
        result = self.ranking.rank([])
        assert len(result) == 0

    def test_rank_normal_weights_with_reranker(self):
        candidates = [
            self._make_candidate("1", "best", reranker_score=0.9),
            self._make_candidate("2", "worst", reranker_score=0.1),
        ]
        result = self.ranking.rank(candidates)
        assert result[0].id == "1"
        # With reranker, best should have higher final_score than worst
        assert result[0].final_score > result[1].final_score

    def test_rank_fallback_weights_without_reranker(self):
        candidates = [
            self._make_candidate("1", "a", rrf_score=0.05),
            self._make_candidate("2", "b", rrf_score=0.01),
        ]
        result = self.ranking.rank(candidates)
        assert result[0].id == "1"

    def test_type_priority_respects_intent(self):
        # With "general" intent (default), raw_information has priority 1.0 vs analysis 0.85
        candidates = [
            self._make_candidate("1", "raw", result_type="raw_information", rrf_score=0.05),
            self._make_candidate("2", "analysis", result_type="analysis", rrf_score=0.05),
        ]
        result = self.ranking.rank(candidates)
        # Both tie on RRF, raw_information has higher type priority in general intent
        assert result[0].result_type == "raw_information"

    def test_entity_boost_affects_ranking(self):
        ctx = SearchContext(
            main_entity=EntityResult(
                name="测试实体", entity_type="company",
                aliases=["with_boost"], ticker=None,
            ),
            entities=[
                EntityResult(
                    name="测试实体", entity_type="company",
                    aliases=["with_boost"], ticker=None,
                ),
            ],
        )
        candidates = [
            self._make_candidate("1", "no_boost", rrf_score=0.1, entity_boost=0.0),
            self._make_candidate("2", "with_boost", rrf_score=0.1, entity_boost=0.0),
        ]
        result = self.ranking.rank(candidates, ctx=ctx)
        # Both have same RRF, same entity_boost (fusion didn't run), but
        # _compute_entity_boost finds "with_boost" in the title for candidate 2
        # and adds 0.08 via alias match, so candidate 2 should win.
        # When RRF has not run (rrf_score=0), the fallback path reads
        # candidate.entity_boost directly, both are 0.0. Set rrf_score > 0
        # to go through the fusion path.
        assert candidates[0].rrf_score > 0
        assert result[0].id == "2"

    def test_to_ranked_items(self):
        candidates = [
            self._make_candidate("1", "test", rrf_score=0.05, reranker_score=0.8),
        ]
        items = self.ranking.to_ranked_items(candidates)
        assert len(items) == 1
        assert items[0].result_type == "raw_information"
        # final_score is set by rank(), not to_ranked_items()
        # items[0].score["total"] reflects c.final_score (0.0 if not ranked)
        assert isinstance(items[0].score["total"], float)

    def test_to_ranked_items_reranker_null_when_zero(self):
        candidates = [
            self._make_candidate("1", "test", rrf_score=0.05, reranker_score=0.0),
        ]
        items = self.ranking.to_ranked_items(candidates)
        assert items[0].score.get("reranker") is None

    # ── Intent-driven weight tests ──

    def test_intent_concept_reduces_reranker(self):
        ctx = SearchContext()
        ctx.timings["dynamic_weights_intent"] = "concept"
        candidates = [
            self._make_candidate("1", "a", rrf_score=0.1, reranker_score=0.9),
            self._make_candidate("2", "b", rrf_score=0.1, reranker_score=0.1),
        ]
        result = self.ranking.rank(candidates, ctx=ctx)
        # concept intent has alpha=0.15, beta=0.35 — RRF matters more than reranker
        # Both have same RRF, reranker still breaks the tie, candidate 1 wins
        assert result[0].id == "1"
        # Verify intent was recorded
        assert ctx.timings["final_ranking_weights"] == "concept_normal"

    def test_intent_news_boosts_time_freshness(self):
        ctx = SearchContext()
        ctx.timings["dynamic_weights_intent"] = "news"
        # Fresh candidate (time_score high) vs stale candidate with better reranker
        c1 = self._make_candidate("1", "old", rrf_score=0.1, reranker_score=0.9)
        c1.time_score = 0.1
        c2 = self._make_candidate("2", "fresh", rrf_score=0.1, reranker_score=0.3)
        c2.time_score = 0.9
        candidates = [c1, c2]
        result = self.ranking.rank(candidates, ctx=ctx)
        # news intent delta=0.35 — freshness has high weight
        # should boost fresh candidate above the old one
        assert result[0].id == "2"

    def test_intent_entity_lookup_boosts_entity_and_type(self):
        ctx = SearchContext(
            main_entity=EntityResult(
                name="测试公司", entity_type="company",
                aliases=["test_co"], ticker="000001",
            ),
            entities=[
                EntityResult(
                    name="测试公司", entity_type="company",
                    aliases=["test_co"], ticker="000001",
                ),
            ],
        )
        ctx.timings["dynamic_weights_intent"] = "entity_lookup"
        # Candidate with entity match in title vs one without
        c1 = self._make_candidate("1", "some random doc", rrf_score=0.1, entity_boost=0.0)
        c2 = self._make_candidate("2", "测试公司 年报", rrf_score=0.1, entity_boost=0.0)
        candidates = [c1, c2]
        result = self.ranking.rank(candidates, ctx=ctx)
        # entity_lookup boosts entity_boost (gamma=0.25), candidate 2 gets
        # boost from _compute_entity_boost matching "测试公司" in title
        assert result[0].id == "2"

    def test_intent_analysis_keeps_reranker_dominant(self):
        ctx = SearchContext()
        ctx.timings["dynamic_weights_intent"] = "analysis"
        candidates = [
            self._make_candidate("1", "good reranker", rrf_score=0.05, reranker_score=0.9),
            self._make_candidate("2", "bad reranker", rrf_score=0.5, reranker_score=0.1),
        ]
        result = self.ranking.rank(candidates, ctx=ctx)
        # analysis intent: alpha=0.50 (reranker dominant), beta=0.20
        # High reranker wins over high RRF
        assert result[0].id == "1"

    def test_missing_intent_falls_back_to_general(self):
        ctx = SearchContext()
        # No dynamic_weights_intent set
        candidates = [
            self._make_candidate("1", "a", rrf_score=0.1, reranker_score=0.9),
            self._make_candidate("2", "b", rrf_score=0.1, reranker_score=0.1),
        ]
        result = self.ranking.rank(candidates, ctx=ctx)
        assert result[0].id == "1"
        assert ctx.timings["final_ranking_weights"] == "general_normal"

    def test_explicit_weights_override_intent(self):
        ctx = SearchContext()
        ctx.timings["dynamic_weights_intent"] = "concept"
        ranking = FinalRanking(
            normal_weights={"alpha": 0.9, "beta": 0.0, "gamma": 0.0, "delta": 0.0, "epsilon": 0.1},
        )
        candidates = [
            self._make_candidate("1", "high reranker", rrf_score=0.05, reranker_score=0.9),
            self._make_candidate("2", "high rrf", rrf_score=0.9, reranker_score=0.1),
        ]
        result = ranking.rank(candidates, ctx=ctx)
        # Explicit weights override concept intent — reranker dominates
        assert result[0].id == "1"
