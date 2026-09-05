from __future__ import annotations

from typing import AsyncGenerator
from uuid import UUID

from kbquant.client._limiter import concurrency_limit
from kbquant.client._base import BaseClient
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.analysis import AnalysisCreate, AnalysisResponse
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.analysis import AnalysisCreate, AnalysisResponse

class AnalysisClient(BaseClient):
    """分析客户端，管理 Agent 分析结果的创建与查询。"""

    @concurrency_limit("insert")
    async def create(self, data: AnalysisCreate) -> AnalysisResponse:
        """提交一条分析结果。

        Args:
            data: 分析数据（分析类型、Agent ID、置信度、内容等）。

        Returns:
            创建成功的分析记录。
        """
        resp = await self._request("POST", "/analysis/", json=data.model_dump(mode="json"))
        return AnalysisResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self,
        *,
        analysis_type: str | None = None,
        agent_id: str | None = None,
        confidence_min: float | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse:
        """分页查询分析记录。

        Args:
            analysis_type: 按分析类型筛选。
            agent_id: 按执行分析的 Agent 筛选。
            confidence_min: 最低置信度阈值（0.0~1.0）。
            page: 页码。
            page_size: 每页数量。

        Returns:
            分页响应。
        """
        params: dict = {"page": page, "page_size": page_size}
        if analysis_type:
            params["analysis_type"] = analysis_type
        if agent_id:
            params["agent_id"] = agent_id
        if confidence_min is not None:
            params["confidence_min"] = confidence_min
        resp = await self._request("GET", "/analysis/", params=params)
        return PaginatedResponse(**resp)

    @concurrency_limit("query")
    async def list_iter(
        self,
        *,
        page_size: int = 100,
        analysis_type: str | None = None,
        agent_id: str | None = None,
        confidence_min: float | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部分析记录，自动翻页。

        Args:
            page_size: 每页大小。
            analysis_type: 按分析类型筛选。
            agent_id: 按 Agent 筛选。
            confidence_min: 最低置信度阈值。

        Yields:
            单条分析记录的 dict。
        """
        params: dict = {}
        if analysis_type:
            params["analysis_type"] = analysis_type
        if agent_id:
            params["agent_id"] = agent_id
        if confidence_min is not None:
            params["confidence_min"] = confidence_min
        async for item in self._paginate("/analysis/", params, page_size):
            yield item

    @concurrency_limit("query")
    async def get(self, analysis_id: str | UUID) -> AnalysisResponse:
        """按 ID 获取分析记录详情。

        Args:
            analysis_id: 分析记录 ID。

        Returns:
            分析记录详情。
        """
        resp = await self._request("GET", f"/analysis/{analysis_id}")
        return AnalysisResponse(**resp)

    @concurrency_limit("query")
    async def get_many(self, analysis_ids: list[str | UUID]) -> list[AnalysisResponse]:
        resp = await self._request("POST", "/analysis/batch", json={"ids": [str(i) for i in analysis_ids]})
        return [AnalysisResponse(**r) for r in resp]
