from __future__ import annotations

from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas.ranking import (
    RankingComputeRequest,
    RankingComputeResponse,
    RankingHistoryResponse,
)


class RankingClient(BaseClient):
    """重要性排序客户端，计算和查询实体/节点的动态重要性评分。"""

    @concurrency_limit("insert")
    async def compute(self, data: RankingComputeRequest) -> RankingComputeResponse:
        """触发一次重要性评分计算。

        Args:
            data: 计算配置（目标类型、计算范围、算法参数等）。

        Returns:
            计算结果摘要。
        """
        resp = await self._request("POST", "/ranking/compute", json=data.model_dump(mode="json"))
        return RankingComputeResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self, *, target_type: str | None = None, min_score: float | None = None, limit: int = 20,
    ) -> list[dict]:
        """查询当前重要性排名。

        Args:
            target_type: 按目标类型筛选。
            min_score: 最低评分阈值。
            limit: 最大返回数量。

        Returns:
            按评分降序排列的条目列表。
        """
        params: dict = {"limit": limit}
        if target_type:
            params["target_type"] = target_type
        if min_score is not None:
            params["min_score"] = min_score
        return await self._request("GET", "/ranking/", params=params)

    @concurrency_limit("query")
    async def get_history(self, target_type: str, target_id: str | UUID) -> list[RankingHistoryResponse]:
        """查询某个目标的重要性评分历史。

        Args:
            target_type: 目标类型。
            target_id: 目标 ID。

        Returns:
            按时间排序的评分历史记录。
        """
        resp = await self._request("GET", f"/ranking/history/{target_type}/{target_id}")
        return [RankingHistoryResponse(**r) for r in resp]