from __future__ import annotations

from typing import AsyncGenerator
from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.node import (
    NodeAttachmentCreate,
    NodeAttachmentResponse,
    NodeCompressionRequest,
    NodeCompressionResponse,
    NodeStateCreate,
    NodeStateResponse,
    WorldNodeCreate,
    WorldNodeResponse,
    NodeNameAliases,
    WorldNodeEdgeCreate,
    WorldNodeEdgeResponse,
)



class NodeClient(BaseClient):
    """世界节点客户端，管理知识图谱 Layer 1 的概念节点及其状态、附件与压缩。"""

    @concurrency_limit("insert")
    async def create(self, data: WorldNodeCreate) -> WorldNodeResponse:
        """创建世界节点，可通过 data.initial_state 一步设置初始状态。

        Args:
            data: 节点数据（名称、类型等），可附带 initial_state。

        Returns:
            创建成功的节点。
        """
        payload = data.model_dump(mode="json", exclude_none=True)
        resp = await self._request("POST", "/nodes/", json=payload)
        return WorldNodeResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self, *, node_type: str | None = None, page: int = 1, page_size: int = 20,
    ) -> PaginatedResponse:
        """分页查询节点列表。

        Args:
            node_type: 按节点类型筛选。
            page: 页码。
            page_size: 每页数量。

        Returns:
            分页响应。
        """
        params: dict = {"page": page, "page_size": page_size}
        if node_type:
            params["node_type"] = node_type
        resp = await self._request("GET", "/nodes/", params=params)
        return PaginatedResponse(**resp)

    @concurrency_limit("query")
    async def list_iter(
        self, *, page_size: int = 100, node_type: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部节点，自动翻页。

        Args:
            page_size: 每页大小。
            node_type: 按节点类型筛选。

        Yields:
            单条节点的 dict。
        """
        params: dict = {}
        if node_type:
            params["node_type"] = node_type
        async for item in self._paginate("/nodes/", params, page_size):
            yield item

    @concurrency_limit("query")
    async def get(self, node_id: str | UUID) -> WorldNodeResponse:
        """按 ID 获取节点详情。

        Args:
            node_id: 节点 ID。

        Returns:
            节点详情。
        """
        resp = await self._request("GET", f"/nodes/{node_id}")
        return WorldNodeResponse(**resp)

    @concurrency_limit("query")
    async def get_many(self, node_ids: list[str | UUID]) -> list[WorldNodeResponse]:
        resp = await self._request("POST", "/nodes/batch", json={"ids": [str(i) for i in node_ids]})
        return [WorldNodeResponse(**r) for r in resp]

    @concurrency_limit("insert")
    async def attach(self, node_id: str | UUID, data: NodeAttachmentCreate) -> NodeAttachmentResponse:
        """给节点挂载附件（资讯、分析结果等）。

        Args:
            node_id: 节点 ID。
            data: 附件数据（附件类型、内容等）。

        Returns:
            创建成功的附件。
        """
        resp = await self._request("POST", f"/nodes/{node_id}/attachments", json=data.model_dump(mode="json"))
        return NodeAttachmentResponse(**resp)

    @concurrency_limit("query")
    async def get_attachments(
        self, node_id: str | UUID, *, role: str | None = None, attachment_type: str | None = None,
    ) -> list[NodeAttachmentResponse]:
        """获取节点的附件列表。

        Args:
            node_id: 节点 ID。
            role: 按附件角色筛选。
            attachment_type: 按附件类型筛选。

        Returns:
            附件列表。
        """
        params: dict = {}
        if role:
            params["role"] = role
        if attachment_type:
            params["attachment_type"] = attachment_type
        resp = await self._request("GET", f"/nodes/{node_id}/attachments", params=params)
        return [NodeAttachmentResponse(**r) for r in resp]

    @concurrency_limit("query")
    async def get_current_state(self, node_id: str | UUID) -> NodeStateResponse:
        """获取节点当前状态快照。

        Args:
            node_id: 节点 ID。

        Returns:
            节点当前状态。
        """
        resp = await self._request("GET", f"/nodes/{node_id}/state/current")
        return NodeStateResponse(**resp)

    @concurrency_limit("insert")
    async def update_state(self, node_id: str | UUID, data: NodeStateCreate) -> NodeStateResponse:
        """更新节点状态（创建新的状态版本）。

        Args:
            node_id: 节点 ID。
            data: 新状态数据。

        Returns:
            创建成功的状态记录。
        """
        resp = await self._request("POST", f"/nodes/{node_id}/state", json=data.model_dump(mode="json"))
        return NodeStateResponse(**resp)

    @concurrency_limit("query")
    async def get_state_history(self, node_id: str | UUID) -> list[NodeStateResponse]:
        """获取节点的状态变更历史。

        Args:
            node_id: 节点 ID。

        Returns:
            按时间排序的状态历史列表。
        """
        resp = await self._request("GET", f"/nodes/{node_id}/state/history")
        return [NodeStateResponse(**r) for r in resp]

    @concurrency_limit("insert")
    async def compress(
        self, node_id: str | UUID, data: NodeCompressionRequest | None = None,
    ) -> NodeCompressionResponse:
        """压缩节点的历史状态，降低存储开销。

        Args:
            node_id: 节点 ID。
            data: 压缩策略配置，为 None 时使用默认策略。

        Returns:
            压缩结果。
        """
        json_body = data.model_dump(mode="json") if data else None
        resp = await self._request("POST", f"/nodes/{node_id}/compress", json=json_body)
        return NodeCompressionResponse(**resp)

    @concurrency_limit("query")
    async def list_names_and_aliases(self) -> list[NodeNameAliases]:
        resp = await self._request("GET", "/nodes/names-aliases")
        return [NodeNameAliases(**r) for r in resp]

    # --- Edge operations ---

    @concurrency_limit("insert")
    async def add_edge(self, data: WorldNodeEdgeCreate) -> WorldNodeEdgeResponse:
        resp = await self._request("POST", "/nodes/edges", json=data.model_dump(mode="json"))
        return WorldNodeEdgeResponse(**resp)

    @concurrency_limit("query")
    async def get_edges(self, node_id: str | UUID) -> list[WorldNodeEdgeResponse]:
        resp = await self._request("GET", f"/nodes/{node_id}/edges")
        return [WorldNodeEdgeResponse(**r) for r in resp]

    @concurrency_limit("insert")
    async def remove_edge(self, edge_id: str | UUID) -> None:
        await self._request("DELETE", f"/nodes/edges/{edge_id}")