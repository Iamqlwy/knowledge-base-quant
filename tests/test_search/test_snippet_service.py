"""Unit tests for snippet_service."""
import pytest

from kbquant.services.search.snippet_service import SnippetService
from kbquant.models.search_candidate import Candidate


class TestSnippetService:
    def setup_method(self):
        self.service = SnippetService()

    def _make_candidate(self, id="1", title="test", snippet="", es_source=None):
        return Candidate(
            id=id, table_name="raw_information", result_type="raw_information",
            title=title, snippet=snippet, rrf_score=0.05, es_source=es_source or {},
        )

    def test_extract_empty_candidates(self):
        result = self.service.extract("test", [])
        assert len(result) == 0

    def test_extract_finds_relevant_window(self):
        c = self._make_candidate(
            id="1",
            title="茅台分析",
            es_source={
                "body": "A" * 100 + " 贵州茅台 业绩下滑 " + "B" * 500,
            },
        )
        result = self.service.extract("贵州茅台 业绩", [c])
        assert len(result) == 1
        assert "贵州茅台" in result[0].snippet or "业绩" in result[0].snippet

    def test_extract_fallback_to_start(self):
        c = self._make_candidate(
            id="1",
            title="short doc",
            es_source={"body": "very short content"},
        )
        result = self.service.extract("unrelated query", [c])
        assert len(result[0].snippet) > 0

    def test_extract_max_length(self):
        c = self._make_candidate(
            id="1",
            title="long doc",
            es_source={"body": "X" * 500},
        )
        result = self.service.extract("test", [c])
        assert len(result[0].snippet) <= 200 + 10  # display_max + ellipsis

    def test_tokenize_query(self):
        tokens = self.service._tokenize_query("贵州茅台 下跌 业绩")
        assert len(tokens) >= 2
        assert "下跌" in tokens or "业绩" in tokens
