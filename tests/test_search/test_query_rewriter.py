"""Unit tests for query_rewriter — stage 2."""
import pytest

from kbquant.services.search.query_rewriter import QueryRewriter
from kbquant.models.search_candidate import SearchContext, EntityResult


class TestQueryRewriter:
    def setup_method(self):
        self.rewriter = QueryRewriter()

    @pytest.mark.asyncio
    async def test_rewrite_expands_synonyms(self):
        result = await self.rewriter.rewrite("贵州茅台 下跌 业绩")
        expanded = result["expanded_keywords"]
        assert "下跌" in expanded or any(
            kw in expanded for kw in ["回调", "回落", "走低", "下行", "下滑", "跌幅"]
        )

    @pytest.mark.asyncio
    async def test_rewrite_includes_original(self):
        result = await self.rewriter.rewrite("贵州茅台")
        assert "贵州茅台" in result["expanded_keywords"]

    @pytest.mark.asyncio
    async def test_rewrite_with_entities(self):
        ctx = SearchContext(
            entities=[
                EntityResult(name="贵州茅台", entity_type="company",
                             aliases=["茅台"], ticker="600519.SH"),
            ]
        )
        result = await self.rewriter.rewrite("贵州茅台 下跌", ctx=ctx)
        expanded = result["expanded_keywords"]
        assert "600519.SH" in expanded or "茅台" in expanded
        assert isinstance(result["entity_context"], dict)

    def test_get_synonyms_forward(self):
        synonyms = self.rewriter.get_synonyms("上涨")
        assert len(synonyms) > 0

    def test_get_synonyms_reverse(self):
        synonyms = self.rewriter.get_synonyms("拉升")
        assert len(synonyms) > 0
        assert "上涨" in synonyms

    def test_get_synonyms_unknown(self):
        synonyms = self.rewriter.get_synonyms("不存在的词xyz123")
        assert len(synonyms) == 0

    @pytest.mark.asyncio
    async def test_rewrite_empty_query(self):
        result = await self.rewriter.rewrite("")
        assert isinstance(result["expanded_keywords"], list)

    @pytest.mark.asyncio
    async def test_rewrite_preserves_entity_context(self):
        ctx = SearchContext(
            entities=[
                EntityResult(name="宁德时代", entity_type="company",
                             aliases=["宁王"], ticker="300750.SZ"),
            ]
        )
        result = await self.rewriter.rewrite("宁德时代", ctx=ctx)
        assert ctx.expanded_keywords is not None
        assert ctx.entity_context is not None
        assert "宁德时代" in ctx.expanded_keywords
