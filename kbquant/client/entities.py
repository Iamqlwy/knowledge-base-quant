from __future__ import annotations

from typing import AsyncGenerator
from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.entity import (
    EntityCreate,
    EntityRelationshipCreate,
    EntityRelationshipResponse,
    EntityResponse,
    ImpactPathResponse,
)


class EntityClient(BaseClient):
    """实体客户端，提供实体的增删改查、关系管理及影响力路径查询。"""

    @concurrency_limit("insert")
    async def create(self, data: EntityCreate) -> EntityResponse:
        """创建新实体。

        Args:
            data: 实体数据（名称、类型、别名、元数据等）。

        Returns:
            创建成功的实体。
        """
        resp = await self._request("POST", "/entities/", json=data.model_dump(mode="json"))
        return EntityResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self,
        *,
        entity_type: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse:
        """分页查询实体列表。

        Args:
            entity_type: 按实体类型筛选（company/person/event 等）。
            search: 按名称模糊搜索。
            page: 页码。
            page_size: 每页数量。

        Returns:
            分页响应。
        """
        params: dict = {"page": page, "page_size": page_size}
        if entity_type:
            params["entity_type"] = entity_type
        if search:
            params["search"] = search
        resp = await self._request("GET", "/entities/", params=params)
        return PaginatedResponse(**resp)

    @concurrency_limit("query")
    async def list_iter(
        self,
        *,
        page_size: int = 100,
        entity_type: str | None = None,
        search: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部实体，自动翻页。

        Args:
            page_size: 每页大小。
            entity_type: 按实体类型筛选。
            search: 按名称模糊搜索。

        Yields:
            单条实体的 dict。
        """
        params: dict = {}
        if entity_type:
            params["entity_type"] = entity_type
        if search:
            params["search"] = search
        async for item in self._paginate("/entities/", params, page_size):
            yield item

    @concurrency_limit("insert")
    async def create_relationship(self, data: EntityRelationshipCreate) -> EntityRelationshipResponse:
        """创建两个实体之间的关系。

        Args:
            data: 关系数据（源实体、目标实体、关系类型、强度等）。

        Returns:
            创建成功的关系。
        """
        resp = await self._request("POST", "/entities/relationships", json=data.model_dump(mode="json"))
        return EntityRelationshipResponse(**resp)

    @concurrency_limit("query")
    async def get_relationships(self, entity_id: str | UUID) -> list[EntityRelationshipResponse]:
        """获取指定实体的所有关系。

        Args:
            entity_id: 实体 ID。

        Returns:
            该实体的所有关系列表。
        """
        resp = await self._request("GET", f"/entities/{entity_id}/relationships")
        return [EntityRelationshipResponse(**r) for r in resp]

    @concurrency_limit("query")
    async def impact_path(
        self, entity_id: str | UUID, *, depth: int = 3, direction: str = "downstream",
    ) -> ImpactPathResponse:
        """查询实体的影响力传导路径。

        Args:
            entity_id: 实体 ID。
            depth: 传导深度，默认 3 层。
            direction: 传导方向（upstream=上游 / downstream=下游）。

        Returns:
            影响力传导路径。
        """
        resp = await self._request(
            "GET", f"/entities/impact-path/{entity_id}",
            params={"depth": depth, "direction": direction},
        )
        return ImpactPathResponse(**resp)