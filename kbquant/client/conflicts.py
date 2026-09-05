from __future__ import annotations
from kbquant.client._limiter import concurrency_limit

from typing import AsyncGenerator
from uuid import UUID

from kbquant.client._limiter import concurrency_limit
from kbquant.client._base import BaseClient
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.conflict import (
    ConflictDetectRequest,
    ConflictDetectResponse,
    ConflictResolveRequest,
    ConflictResponse,
)

class ConflictClient(BaseClient):
    """冲突检测客户端，检测和解决知识图谱中的信息冲突。"""

    @concurrency_limit("insert")
    async def detect(self, data: ConflictDetectRequest) -> ConflictDetectResponse:
        """检测知识图谱中的信息冲突。

        Args:
            data: 检测范围配置（节点 ID、冲突类型等）。

        Returns:
            检测到的冲突列表。
        """
        resp = await self._request("POST", "/conflicts/detect", json=data.model_dump(mode="json"))
        return ConflictDetectResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self,
        *,
        node_id: UUID | None = None,
        conflict_type: str | None = None,
        resolved: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse:
        """分页查询冲突记录。

        Args:
            node_id: 按节点筛选。
            conflict_type: 按冲突类型筛选。
            resolved: 是否已解决。
            page: 页码。
            page_size: 每页数量。

        Returns:
            分页响应。
        """
        params: dict = {"page": page, "page_size": page_size}
        if node_id:
            params["node_id"] = str(node_id)
        if conflict_type:
            params["conflict_type"] = conflict_type
        if resolved is not None:
            params["resolved"] = resolved
        resp = await self._request("GET", "/conflicts/", params=params)
        return PaginatedResponse(**resp)

    @concurrency_limit("query")
    async def list_iter(
        self,
        *,
        page_size: int = 100,
        node_id: UUID | None = None,
        conflict_type: str | None = None,
        resolved: bool | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部冲突记录，自动翻页。

        Args:
            page_size: 每页大小。
            node_id: 按节点筛选。
            conflict_type: 按冲突类型筛选。
            resolved: 是否已解决。

        Yields:
            单条冲突记录的 dict。
        """
        params: dict = {}
        if node_id:
            params["node_id"] = str(node_id)
        if conflict_type:
            params["conflict_type"] = conflict_type
        if resolved is not None:
            params["resolved"] = resolved
        async for item in self._paginate("/conflicts/", params, page_size):
            yield item

    @concurrency_limit("insert")
    async def resolve(self, conflict_id: str | UUID, data: ConflictResolveRequest) -> ConflictResponse:
        """解决一条冲突。

        Args:
            conflict_id: 冲突 ID。
            data: 解决方案（选择的版本、理由等）。

        Returns:
            解决后的冲突记录。
        """
        resp = await self._request(
            "PUT", f"/conflicts/{conflict_id}/resolve", json=data.model_dump(mode="json"),
        )
        return ConflictResponse(**resp)
