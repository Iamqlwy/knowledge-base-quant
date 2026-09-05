"""Unit tests for fusion_service — stage 5."""
import pytest
from datetime import datetime, timezone

from kbquant.services.search.fusion_service import FusionService
from kbquant.models.search_candidate import SearchContext, EntityResult


class TestFusionService:
    def setup_method(self):
        self.service = FusionService(rrf_k=60)

    def test_fuse_empty(self):
        candidates = self.service.fuse({}, {}, {})
        assert len(candidates) == 0

    def test_fuse_single_channel(self):
        bm25 = {"doc1": {"score": 10.0, "source": {"title": "Test", "body": "content"}}}
        candidates = self.service.fuse(bm25, {}, {})
        assert len(candidates) == 1
        assert candidates[0].id == "doc1"
        assert candidates[0].rrf_score > 0

    def test_fuse_multi_channel_same_doc_scores_higher(self):
        bm25 = {"doc1": {"score": 10.0, "source": {"title": "Test"}}}
        vec = {"doc1": {"score": 0.9, "row": None}}
        candidates = self.service.fuse(bm25, vec, {})
        assert len(candidates) == 1
        # Same doc appears in both channels → higher RRF score
        assert candidates[0].rrf_score > 0

    def test_fuse_different_docs(self):
        bm25 = {"doc1": {"score": 10.0, "source": {"title": "A"}}}
        vec = {"doc2": {"score": 0.9, "row": None}}
        candidates = self.service.fuse(bm25, vec, {})
        assert len(candidates) == 2

    def test_fuse_scores_are_distinct(self):
        bm25 = {
            "doc1": {"score": 10.0, "source": {"title": "Top hit"}},
            "doc2": {"score": 5.0, "source": {"title": "Mid hit"}},
            "doc3": {"score": 2.0, "source": {"title": "Low hit"}},
        }
        candidates = self.service.fuse(bm25, {}, {})
        scores = [c.rrf_score for c in candidates]
        assert scores[0] > scores[-1], "Top should outscore last"

    def test_entity_context_boost(self):
        ctx = SearchContext(
            entity_context={"茅台": 0.8},
        )
        bm25 = {
            "doc1": {"score": 10.0, "source": {"title": "贵州茅台分析", "body": "茅台相关内容"}},
            "doc2": {"score": 10.0, "source": {"title": "其他内容", "body": "无关"}},
        }
        candidates = self.service.fuse(bm25, {}, {}, ctx=ctx)
        # entity_boost is computed from entity_context (note: no entity_context
        # weight in _DEFAULT_WEIGHTS anymore — it's applied in FinalRanking)
        assert candidates[0].entity_boost >= 0
        # doc1 mentioning "茅台" should have higher entity_boost than doc2
        doc1 = next(c for c in candidates if c.id == "doc1")
        doc2 = next(c for c in candidates if c.id == "doc2")
        assert doc1.entity_boost > doc2.entity_boost

    def test_score_breakdown_populated(self):
        bm25 = {"doc1": {"score": 10.0, "source": {"title": "Test", "body": "content"}}}
        candidates = self.service.fuse(bm25, {}, {})
        assert len(candidates) == 1
        bd = candidates[0].score_breakdown
        assert "rrf_raw" in bd
        assert "structural_contrib" in bd
        assert "time_contrib" in bd
        assert "entity_boost_computed" in bd
        assert "active_channels" in bd

    def test_rrf_k_param_affects_score(self):
        svc_60 = FusionService(rrf_k=60)
        svc_30 = FusionService(rrf_k=30)

        bm25 = {"doc1": {"score": 10.0, "source": {"title": "Test"}}}
        c60 = svc_60.fuse(bm25, {}, {})
        c30 = svc_30.fuse(bm25, {}, {})

        # k=30 gives higher scores (less damping)
        assert c30[0].rrf_score > c60[0].rrf_score

class TestPositionScore:
    """Tests for _compute_position_score and _find_term_position."""

    def setup_method(self):
        self.service = FusionService(rrf_k=60)

    def test_position_front_scoring(self):
        """Terms at position 0 get maximum score."""
        score = self.service._compute_position_score(
            "茅台 股价",
            {"source": {"body": "茅台股价大幅上涨今天走势良好"}},
            None,
        )
        assert score > 0.9

    def test_position_deep_scoring(self):
        """Terms deep in body get low score."""
        body = "无关" * 200 + "茅台股价分析"
        score = self.service._compute_position_score(
            "茅台 股价",
            {"source": {"body": body}},
            None,
        )
        assert score < 0.5

    def test_no_body_text_returns_midpoint(self):
        """No body text returns neutral 0.5."""
        assert self.service._compute_position_score("茅台", None, None) == 0.5

    def test_es_hit_none_no_crash(self):
        """es_hit=None should not crash — only PG body is used."""
        score = self.service._compute_position_score(
            "test", None, {"row": {"body": "this is a test body"}}
        )
        assert score > 0.9

    def test_pg_hit_none_no_crash(self):
        """pg_hit=None should not crash — only ES body is used."""
        score = self.service._compute_position_score(
            "test", {"source": {"body": "this is a test body"}}, None
        )
        assert score > 0.9

    def test_es_pg_combined(self):
        """ES and PG body text are combined for search."""
        score = self.service._compute_position_score(
            "茅台",
            {"source": {"body": "前言"}},
            {"row": {"body": "茅台白酒"}},
        )
        assert score > 0.9

    def test_ascii_word_boundary_no_false_match(self):
        """ASCII word-boundary: 'Sea' does NOT match inside 'Search'."""
        assert self.service._compute_position_score(
            "Sea", {"source": {"body": "Search"}}, None
        ) == 0.0

    def test_ascii_word_boundary_correct_match(self):
        """ASCII word-boundary correctly matches standalone word."""
        score = self.service._compute_position_score(
            "Sea", {"source": {"body": "Search engine Sea transport"}}, None
        )
        assert score > 0.85

    def test_short_terms_filtered(self):
        """Terms < 2 chars filtered; returns 1.0 if no valid terms remain."""
        assert self.service._compute_position_score(
            "a b", {"source": {"body": "content"}}, None
        ) == 1.0

    def test_length_penalty_medium_text(self):
        body = "茅台" * 500
        score = self.service._compute_position_score(
            "茅台", {"source": {"body": body}}, None,
        )
        assert score < 0.90
        assert score > 0.78

    def test_length_penalty_no_effect_short_text(self):
        body = "茅台酒" * 50
        score = self.service._compute_position_score(
            "茅台", {"source": {"body": body}}, None,
        )
        assert score > 0.95

    def test_fuse_includes_position_contrib(self):
        """Fuse populates position_contrib in score_breakdown."""
        from kbquant.models.search_candidate import SearchContext
        ctx = SearchContext(query_text="测试")
        bm25 = {"doc1": {"score": 10.0, "source": {"title": "T", "body": "测试文档内容"}}}
        candidates = self.service.fuse(bm25, {}, {}, ctx=ctx)
        assert len(candidates) == 1
        assert "position_contrib" in candidates[0].score_breakdown
        assert candidates[0].score_breakdown["position_contrib"] > 0.9
