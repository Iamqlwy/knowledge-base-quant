"""Unit tests for hard_filter — stage 4."""
import pytest

from kbquant.services.search.hard_filter import HardFilter
from kbquant.models.search_candidate import Candidate, SearchContext, EntityResult


def _make_raw_result(title="test", body="", score=1.0, published_at=None):
    return {
        "score": score,
        "source": {"title": title, "body": body},
    }


def _make_pg_result(title="test", body="", score=0.5, published_at=None):
    """Simulate a pgvector result with a row-like object."""
    class FakeRow:
        def __init__(self):
            self.title = title
            self.body = body
            self.content = ""
            self.published_at = published_at
            self.created_at = None
            self.updated_at = None
    return {"score": score, "row": FakeRow()}


class TestHardFilterRaw:
    """Tests for filter_raw_results (before fusion)."""

    def setup_method(self):
        self.filter = HardFilter(vector_low_threshold=0.3)

    def test_no_filter_when_no_main_entity(self):
        bm25 = {"1": _make_raw_result("中国广核FCD深度分析")}
        ctx = SearchContext()
        bm25_out, pg_out, nm_out, dropped = self.filter.filter_raw_results(
            bm25, {}, {}, ctx=ctx,
        )
        assert len(bm25_out) == 1

    def test_stock_entity_hard_filter_removes_irrelevant(self):
        ctx = SearchContext(
            main_entity=EntityResult(
                name="贵州茅台", entity_type="company",
                aliases=["茅台"], ticker="600519.SH",
            ),
        )
        bm25 = {
            "1": _make_raw_result("茅台代销政策落地", "渠道变革..."),
            "2": _make_raw_result("中国广核FCD分析", "常规工程..."),
            "3": _make_raw_result("酒价内参价格发布", "茅台批价供需关系..."),
        }
        bm25_out, pg_out, nm_out, dropped = self.filter.filter_raw_results(
            bm25, {}, {}, ctx=ctx,
        )
        assert len(bm25_out) == 2
        titles = [v["source"]["title"] for v in bm25_out.values()]
        assert "茅台代销政策落地" in titles
        assert "酒价内参价格发布" in titles
        assert "中国广核FCD分析" not in titles
        assert dropped.get("entity_absent", 0) == 1

    def test_non_stock_entity_no_hard_filter(self):
        ctx = SearchContext(
            main_entity=EntityResult(
                name="半导体", entity_type="sector",
                aliases=["芯片"], ticker=None,
            ),
        )
        bm25 = {
            "1": _make_raw_result("中国广核FCD分析"),
            "2": _make_raw_result("上海算力建设"),
        }
        bm25_out, pg_out, nm_out, dropped = self.filter.filter_raw_results(
            bm25, {}, {}, ctx=ctx,
        )
        assert len(bm25_out) == 2

    def test_vector_low_threshold_filters(self):
        ctx = SearchContext()
        pg = {
            "1": _make_pg_result("doc1", score=0.5),
            "2": _make_pg_result("doc2", score=0.1),
            "3": _make_pg_result("doc3", score=0.0),
        }
        bm25_out, pg_out, nm_out, dropped = self.filter.filter_raw_results(
            {}, pg, {}, ctx=ctx,
        )
        assert len(pg_out) == 2  # doc2 removed, doc3 kept (0 = no vector)
        assert dropped.get("vector_low", 0) == 1

    def test_stock_filter_matches_ticker(self):
        ctx = SearchContext(
            main_entity=EntityResult(
                name="贵州茅台", entity_type="company",
                aliases=["茅台"], ticker="600519",
            ),
        )
        bm25 = {"1": _make_raw_result("600519 下跌分析")}
        bm25_out, pg_out, nm_out, dropped = self.filter.filter_raw_results(
            bm25, {}, {}, ctx=ctx,
        )
        assert len(bm25_out) == 1

    def test_stock_filter_matches_alias(self):
        ctx = SearchContext(
            main_entity=EntityResult(
                name="宁德时代", entity_type="company",
                aliases=["宁王", "300750"], ticker="300750.SZ",
            ),
        )
        bm25 = {"1": _make_raw_result("宁王业绩超预期")}
        bm25_out, pg_out, nm_out, dropped = self.filter.filter_raw_results(
            bm25, {}, {}, ctx=ctx,
        )
        assert len(bm25_out) == 1


class TestHardFilterBoosts:
    """Tests for apply_boosts (after fusion, soft rules only)."""

    def setup_method(self):
        self.filter = HardFilter()

    def _make_candidate(self, id="1", title="test", snippet=""):
        return Candidate(
            id=id,
            table_name="raw_information",
            result_type="raw_information",
            title=title,
            snippet=snippet,
        )

    def test_keyword_coverage_continuous_demote(self):
        """Coverage 0/4 → multiplier 0.5, 4/4 → multiplier 1.0"""
        ctx = SearchContext()
        ctx.expanded_keywords = {"茅台", "下跌", "业绩", "公告"}
        candidates = [
            self._make_candidate("1", "中芯国际利好分析"),  # 0/4 → 0.5
            self._make_candidate("2", "茅台下跌业绩公告"),  # 4/4 → 1.0
        ]
        candidates[0].entity_boost = 1.0
        candidates[1].entity_boost = 1.0
        result = self.filter.apply_boosts(candidates, ctx=ctx)
        # 0/4: penalty_mult = 1 - (1-0)*0.5 = 0.5
        assert result[0].entity_boost == 0.5
        # 4/4: penalty_mult = 1 - (1-1)*0.5 = 1.0
        assert result[1].entity_boost == 1.0

    def test_no_demote_with_few_keywords(self):
        ctx = SearchContext()
        ctx.expanded_keywords = {"茅台", "下跌"}  # only 2 keywords
        candidates = [
            self._make_candidate("1", "无关文档"),
        ]
        candidates[0].entity_boost = 1.0
        result = self.filter.apply_boosts(candidates, ctx=ctx)
        assert result[0].entity_boost == 1.0  # no demote


    def test_entity_context_boost_applied(self):
        """Entity context boosts are now applied in FusionService (not HardFilter).
        HardFilter only does demotion now; entity_context boost is a fusion concern."""
        pass

    def test_entity_context_boost_capped(self):
        """Entity context boosts are applied in FusionService (not HardFilter)."""
        pass
