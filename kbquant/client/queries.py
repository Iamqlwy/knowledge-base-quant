from __future__ import annotations

from datetime import datetime
from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit


class QueriesClient(BaseClient):
    """时点查询客户端，支持 as-of 时间点状态还原和状态对比。"""

    @concurrency_limit("query")
    async def as_of(
        self,
        timestamp: datetime,
        *,
        node_id: UUID | None = None,
        entity_id: UUID | None = None,
        info_id: UUID | None = None,
    ) -> dict:
        """查询某个时间点的系统状态快照。

        Args:
            timestamp: 目标时间点。
            node_id: 按节点 ID 过滤。
            entity_id: 按实体 ID 过滤。
            info_id: 按资讯 ID 过滤。

        Returns:
            该时间点的状态快照。
        """
        params: dict = {}
        if node_id:
            params["node_id"] = str(node_id)
        if entity_id:
            params["entity_id"] = str(entity_id)
        if info_id:
            params["info_id"] = str(info_id)
        return await self._request(
            "GET", f"/queries/as-of/{timestamp.isoformat()}", params=params,
        )

    @concurrency_limit("query")
    async def diff_state(
        self, *, node_id: UUID, timestamp_a: datetime, timestamp_b: datetime,
    ) -> dict:
        """对比两个时间点的节点状态差异。

        Args:
            node_id: 节点 ID。
            timestamp_a: 时间点 A。
            timestamp_b: 时间点 B。

        Returns:
            状态差异对比结果。
        """
        return await self._request(
            "POST", "/queries/as-of-diff",
            params={
                "node_id": str(node_id),
                "timestamp_a": timestamp_a.isoformat(),
                "timestamp_b": timestamp_b.isoformat(),
            },
        )

    @concurrency_limit("query")
    async def get_state_at(self, node_id: str | UUID, timestamp: datetime) -> dict:
        """获取某个节点在指定时间点的状态。

        Args:
            node_id: 节点 ID。
            timestamp: 目标时间点。

        Returns:
            节点状态快照。
        """
        return await self._request(
            "GET", f"/queries/nodes/{node_id}/state/at/{timestamp.isoformat()}",
        )
