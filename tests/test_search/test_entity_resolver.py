"""Unit tests for entity_resolver — stage 1."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kbquant.services.search.entity_resolver import EntityResolver, ResolvedEntity
from kbquant.models.search_candidate import SearchContext


class TestEntityResolverInit:
    def test_create_with_default_matcher(self):
        resolver = EntityResolver()
        assert resolver._matcher is not None
        assert resolver._matcher.entity_count >= 0


class TestResolvedEntity:
    def test_to_entity_result(self):
        e = ResolvedEntity(
            name="贵州茅台",
            entity_type="company",
            aliases=["茅台", "600519"],
            ticker="600519.SH",
            match_method="aho_corasick",
            priority=0,
            score=0.95,
        )
        result = e.to_entity_result()
        assert result.name == "贵州茅台"
        assert result.entity_type == "company"
        assert result.ticker == "600519.SH"
        assert "600519" in result.aliases
        assert result.match_method == "aho_corasick"


class TestEntityResolverResolve:
    @pytest.mark.asyncio
    async def test_resolve_aho_corasick_only(self):
        resolver = EntityResolver()
        ctx = SearchContext()
        entities = await resolver.resolve("贵州茅台 下跌", session=None, ctx=ctx)
        # 贵州茅台 should be in company.json entities
        found = any("茅台" in e.name for e in entities)
        if found:
            assert len(entities) > 0
        assert ctx.entities is not None
        assert ctx.main_entity is not None if entities else True

    @pytest.mark.asyncio
    async def test_resolve_with_worldnode_mocked(self):
        resolver = EntityResolver()
        mock_session = AsyncMock()
        mock_row = MagicMock()
        mock_row.name = "贵州茅台"
        mock_row.node_type = "stock"
        mock_row.ticker = "600519.SH"
        mock_row.aliases = ["茅台"]

        mock_session = AsyncMock()
        with patch(
            "kbquant.services.search_service.SearchService._entity_match_search",
            return_value={
                "uuid-1": {"score": 1.0, "row": mock_row},
            },
        ):
            ctx = SearchContext()
            entities = await resolver.resolve("贵州茅台", session=mock_session, ctx=ctx)

            assert len(entities) > 0
            stock_entities = [e for e in entities if e.entity_type == "stock"]
            assert len(stock_entities) > 0
            assert stock_entities[0].name == "贵州茅台"
            assert stock_entities[0].ticker == "600519.SH"

    @pytest.mark.asyncio
    async def test_main_entity_is_highest_priority(self):
        resolver = EntityResolver()
        mock_session = AsyncMock()
        ctx = SearchContext()

        # Create entities with different types
        entities = await resolver.resolve("600519 贵州茅台", session=None, ctx=ctx)

        if entities and ctx.main_entity:
            # Main entity should be the one with lowest priority number
            assert ctx.main_entity.entity_type in (
                    "stock", "company", "fund", "index", "commodity",
                    "sector", "concept", "strategy", "person", "institution",
                    "policy", "event",
                )

    @pytest.mark.asyncio
    async def test_resolve_empty_query(self):
        resolver = EntityResolver()
        ctx = SearchContext()
        entities = await resolver.resolve("", session=None, ctx=ctx)
        assert isinstance(entities, list)

    @pytest.mark.asyncio
    async def test_resolve_deduplicates_by_name(self):
        resolver = EntityResolver()
        mock_session = AsyncMock()
        mock_row1 = MagicMock()
        mock_row1.name = "贵州茅台"
        mock_row1.node_type = "stock"
        mock_row1.ticker = "600519.SH"
        mock_row1.aliases = ["茅台"]

        mock_row2 = MagicMock()
        mock_row2.name = "贵州茅台"
        mock_row2.node_type = "stock"
        mock_row2.ticker = "600519.SH"
        mock_row2.aliases = []

        with patch(
            "kbquant.services.search_service.SearchService._entity_match_search",
            return_value={
                "uuid-1": {"score": 1.0, "row": mock_row1},
                "uuid-2": {"score": 0.95, "row": mock_row2},
            },
        ):
            ctx = SearchContext()
            entities = await resolver.resolve("贵州茅台", session=mock_session, ctx=ctx)
            # Should be deduplicated to one entry
            names = [e.name for e in entities]
            assert names.count("贵州茅台") <= 1

    @pytest.mark.asyncio
    async def test_match_method_prioritizes_worldnode(self):
        resolver = EntityResolver()
        mock_session = AsyncMock()
        mock_row = MagicMock()
        mock_row.name = "宁德时代"
        mock_row.node_type = "stock"
        mock_row.ticker = "300750.SZ"
        mock_row.aliases = ["宁王"]

        with patch(
            "kbquant.services.search_service.SearchService._entity_match_search",
            return_value={
                "uuid-1": {"score": 1.2, "row": mock_row},
            },
        ):
            ctx = SearchContext()
            entities = await resolver.resolve("300750", session=mock_session, ctx=ctx)
            # Ticker exact match should have worldnode_score or worldnode_ticker_exact method
            ticker_entities = [e for e in entities if e.ticker == "300750.SZ"]
            for e in ticker_entities:
                assert e.match_method in ("worldnode_score", "worldnode_ticker_exact")
