from __future__ import annotations

from typing import AsyncGenerator
from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.schemas.pipeline import PipelineStats, PipelineStatusUpdate, ReprioritizeRequest
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas.pipeline import PipelineStats, PipelineStatusUpdate, ReprioritizeRequest


class PipelineClient(BaseClient):
    """处理管线客户端，管理资讯处理队列、状态和优先级。"""

    @concurrency_limit("query")
    async def list_queue(
        self,
        *,
        status: str | list[str] | None = None,
        priority_min: int | None = None,
        agent_assigned: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """分页查询处理队列。

        Args:
            status: 按处理状态筛选（单个状态或状态列表）。
            priority_min: 按最低优先级筛选。
            agent_assigned: 按分配的 Agent 筛选。
            page: 页码。
            page_size: 每页数量。

        Returns:
            队列项列表及总数。
        """
        params: dict = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        if priority_min is not None:
            params["priority_min"] = priority_min
        if agent_assigned:
            params["agent_assigned"] = agent_assigned
        return await self._request("GET", "/pipeline/queue", params=params)

    @concurrency_limit("query")
    async def list_queue_iter(
        self,
        *,
        page_size: int = 100,
        status: str | list[str] | None = None,
        priority_min: int | None = None,
        agent_assigned: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部队列项，自动翻页。

        Args:
            page_size: 每页大小。
            status: 按状态筛选（单个状态或状态列表）。
            priority_min: 按最低优先级筛选。
            agent_assigned: 按 Agent 筛选。

        Yields:
            单条队列项的 dict。
        """
        params: dict = {}
        if status:
            params["status"] = status
        if priority_min is not None:
            params["priority_min"] = priority_min
        if agent_assigned:
            params["agent_assigned"] = agent_assigned
        async for item in self._paginate("/pipeline/queue", params, page_size):
            yield item

    @concurrency_limit("insert")
    async def update_status(self, raw_info_id: str | UUID, data: PipelineStatusUpdate) -> dict:
        """更新某条资讯的处理状态。

        Args:
            raw_info_id: 资讯 ID。
            data: 新状态及附加信息。

        Returns:
            更新后的队列项。
        """
        return await self._request(
            "PUT", f"/pipeline/{raw_info_id}/status", json=data.model_dump(mode="json"),
        )

    @concurrency_limit("query")
    async def stats(self) -> PipelineStats:
        """获取处理管线的统计信息。

        Returns:
            各状态下的队列项数量及平均处理时间。
        """
        resp = await self._request("GET", "/pipeline/stats")
        return PipelineStats(**resp)

    @concurrency_limit("insert")
    async def reprioritize(self, data: ReprioritizeRequest) -> dict:
        """批量调整队列项优先级。

        Args:
            data: 包含条目 ID 列表和新优先级值。

        Returns:
            已更新的条目数量。
        """
        return await self._request("POST", "/pipeline/reprioritize", json=data.model_dump(mode="json"))
