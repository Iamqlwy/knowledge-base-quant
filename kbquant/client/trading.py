from __future__ import annotations

from typing import AsyncGenerator
from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.trading import (
    TradingOperationCreate,
    TradingOperationResponse,
    TradingOperationUpdate,
)


class TradingClient(BaseClient):
    """交易操作客户端，记录和查询量化交易操作。"""

    @concurrency_limit("insert")
    async def create(self, data: TradingOperationCreate) -> TradingOperationResponse:
        """记录一条交易操作。

        Args:
            data: 交易操作数据（类型、标的、数量、价格、触发节点等）。

        Returns:
            创建成功的交易操作记录。
        """
        resp = await self._request("POST", "/trading/", json=data.model_dump(mode="json"))
        return TradingOperationResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self,
        *,
        operation_type: str | None = None,
        node_id: UUID | None = None,
        symbol: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse:
        """分页查询交易操作记录。

        Args:
            operation_type: 按操作类型筛选（buy/sell/hold 等）。
            node_id: 按触发节点筛选。
            symbol: 按交易标的代码筛选。
            status: 按执行状态筛选。
            page: 页码。
            page_size: 每页数量。

        Returns:
            分页响应。
        """
        params: dict = {"page": page, "page_size": page_size}
        if operation_type:
            params["operation_type"] = operation_type
        if node_id:
            params["node_id"] = str(node_id)
        if symbol:
            params["symbol"] = symbol
        if status:
            params["status"] = status
        resp = await self._request("GET", "/trading/", params=params)
        return PaginatedResponse(**resp)

    @concurrency_limit("query")
    async def list_iter(
        self,
        *,
        page_size: int = 100,
        operation_type: str | None = None,
        node_id: UUID | None = None,
        symbol: str | None = None,
        status: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部交易操作记录，自动翻页。

        Args:
            page_size: 每页大小。
            operation_type: 按操作类型筛选。
            node_id: 按触发节点筛选。
            symbol: 按交易标的筛选。
            status: 按执行状态筛选。

        Yields:
            单条交易操作记录的 dict。
        """
        params: dict = {}
        if operation_type:
            params["operation_type"] = operation_type
        if node_id:
            params["node_id"] = str(node_id)
        if symbol:
            params["symbol"] = symbol
        if status:
            params["status"] = status
        async for item in self._paginate("/trading/", params, page_size):
            yield item

    @concurrency_limit("insert")
    async def update(
        self, trade_id: str | UUID, data: TradingOperationUpdate
    ) -> TradingOperationResponse:
        resp = await self._request(
            "PUT", f"/trading/{trade_id}", json=data.model_dump(mode="json")
        )
        return TradingOperationResponse(**resp)

    @concurrency_limit("query")
    async def get(self, trade_id: str | UUID) -> TradingOperationResponse:
        """按 ID 获取交易操作详情。

        Args:
            trade_id: 交易操作 ID。

        Returns:
            交易操作详情。
        """
        resp = await self._request("GET", f"/trading/{trade_id}")
        return TradingOperationResponse(**resp)

    @concurrency_limit("query")
    async def get_many(self, trade_ids: list[str | UUID]) -> list[TradingOperationResponse]:
        resp = await self._request("POST", "/trading/batch", json={"ids": [str(i) for i in trade_ids]})
        return [TradingOperationResponse(**r) for r in resp]