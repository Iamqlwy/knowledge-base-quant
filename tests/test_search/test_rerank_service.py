"""Unit tests for rerank_service — stage 6 (sidecar client)."""
import pytest
from unittest.mock import patch

from kbquant.services.search.rerank_service import RerankService
from kbquant.models.search_candidate import Candidate


class TestRerankService:
    def _make_candidate(self, id="1", title="test", snippet="", raw=None):
        return Candidate(
            id=id, table_name="raw_information", result_type="raw_information",
            title=title, snippet=snippet, raw=raw, rrf_score=0.05, bm25_score=1.0,
        )

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self):
        svc = RerankService()
        result = await svc.rerank("test query", [])
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_sidecar_call_success(self):
        svc = RerankService()
        candidates = [
            self._make_candidate("1", "茅台分析"),
            self._make_candidate("2", "广核分析"),
        ]

        with patch("kbquant.integrations.sidecar.client._post") as mock_post:
            mock_post.return_value = {"scores": [0.95, 0.30]}
            result = await svc.rerank("贵州茅台 下跌", candidates)
            assert len(result) == 2
            assert result[0].reranker_score > result[1].reranker_score
            assert result[0].id == "1"

    @pytest.mark.asyncio
    async def test_sidecar_failure_falls_back(self):
        svc = RerankService()
        candidates = [
            self._make_candidate("1", "doc1"),
            self._make_candidate("2", "doc2"),
        ]

        with patch("kbquant.integrations.sidecar.client._post",
                   side_effect=Exception("Connection refused")):
            result = await svc.rerank("test query", candidates)
            assert len(result) == 2
            assert all(c.reranker_score == 0.0 for c in result)

    @pytest.mark.asyncio
    async def test_queue_depth_gate_skips(self):
        svc = RerankService()
        candidates = [self._make_candidate("1", "doc1")]

        # Artificially saturate the pending counter
        RerankService._pending_count = 50
        try:
            result = await svc.rerank("test", candidates)
            assert result == candidates
            assert candidates[0].reranker_score == 0.0
        finally:
            RerankService._pending_count = 0

    @pytest.mark.asyncio
    async def test_rerank_scores_set_correctly(self):
        svc = RerankService()
        candidates = [
            self._make_candidate("a", "docA"),
            self._make_candidate("b", "docB"),
            self._make_candidate("c", "docC"),
        ]

        with patch("kbquant.integrations.sidecar.client._post") as mock_post:
            mock_post.return_value = {"scores": [0.9, 0.1, 0.5]}
            result = await svc.rerank("query", candidates)

            # All candidates returned
            assert len(result) == 3
            # Top-N limited to _RERANK_TOP_N=50 but we only have 3, so all get scores
            scores = {c.id: c.reranker_score for c in result}
            assert scores["a"] == 0.9
            assert scores["b"] == 0.1
            assert scores["c"] == 0.5
            # Sorted by reranker_score descending
            assert result[0].id == "a"
            assert result[1].id == "c"
            assert result[2].id == "b"
