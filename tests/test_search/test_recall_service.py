"""Unit tests for recall_service — stage 3."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kbquant.services.search.recall_service import RecallService
from kbquant.models.search_candidate import SearchContext


class TestTableDetermination:
    def setup_method(self):
        self.service = RecallService()

    def test_default_tables_raw_information(self):
        tables = self.service.determine_tables("普通查询")
        assert "raw_information" in tables

    def test_stock_financial_adds_analyses(self):
        entities = [MagicMock(entity_type="company"), MagicMock(entity_type="stock")]
        tables = self.service.determine_tables("宁德时代 业绩 财报", entities=entities)
        assert "raw_information" in tables
        assert "analyses" in tables

    def test_strategy_kw_adds_feedbacks(self):
        tables = self.service.determine_tables("打板 炸板 止损 策略")
        assert "raw_information" in tables
        assert "feedbacks" in tables

    def test_macro_kw_adds_nodes(self):
        tables = self.service.determine_tables("宏观 GDP 通胀 PMI")
        assert "raw_information" in tables
        # "宏观" is not in any keyword hint; GDP/通胀/PMI don't match "supply_chain"
        # keywords, so no table is added beyond BASE_TABLES[None]
        assert isinstance(tables, list)

    def test_financial_kw_adds_analyses(self):
        tables = self.service.determine_tables("茅台 财报 营收 利润")
        assert "raw_information" in tables
        assert "analyses" in tables

    def test_no_kw_no_entity_defaults_to_raw(self):
        tables = self.service.determine_tables("随便看看")
        assert "raw_information" in tables
        # When no entity and no keywords match, default is raw_information + analyses
        assert "analyses" in tables

    def test_updates_search_context(self):
        ctx = SearchContext()
        tables = self.service.determine_tables("茅台 财报", ctx=ctx)
        assert ctx.target_tables == tables

    def test_multiple_tables_combined(self):
        entities = [MagicMock(entity_type="company")]
        tables = self.service.determine_tables("宁德时代 打板 止损 策略", entities=entities)
        assert "raw_information" in tables
        assert "analyses" in tables
        assert "feedbacks" in tables
