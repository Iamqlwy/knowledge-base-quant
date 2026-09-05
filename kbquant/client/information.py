from __future__ import annotations

from typing import Any, AsyncGenerator
from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.entity import EntityExtractRequest
from kbquant.schemas.information import (
    BatchUpdateImportanceRequest,
    BatchUpdateImportanceResponse,
    DedupCheckRequest,
    DedupCheckResponse,
    InformationMergeRequest,
    InformationMergeResponse,
    RawInformationCreate,
    RawInformationResponse,
)


class InformationClient(BaseClient):
    """资讯客户端，提供资讯录入、查询、去重、合并及实体提取功能。"""

    @concurrency_limit("insert")
    async def ingest(self, data: RawInformationCreate) -> RawInformationResponse:
        """录入一条原始资讯，自动进入处理管线。

        Args:
            data: 资讯数据（标题、正文、来源、发布时间等）。

        Returns:
            创建成功的资讯记录。
        """
        resp = await self._request("POST", "/information/", json=data.model_dump(mode="json"))
        return RawInformationResponse(**resp)

    @concurrency_limit("query")
    async def get(self, info_id: str | UUID) -> RawInformationResponse:
        """按 ID 获取单条资讯。

        Args:
            info_id: 资讯的唯一标识。

        Returns:
            资讯详情。
        """
        resp = await self._request("GET", f"/information/{info_id}")
        return RawInformationResponse(**resp)

    @concurrency_limit("query")
    async def get_many(self, info_ids: list[str | UUID]) -> list[RawInformationResponse]:
        """按 ID 列表批量获取资讯。

        Args:
            info_ids: 资讯 ID 列表。

        Returns:
            资讯详情列表。
        """
        resp = await self._request("POST", "/information/batch", json={"ids": [str(i) for i in info_ids]})
        return [RawInformationResponse(**r) for r in resp]

    @concurrency_limit("query")
    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        info_type: str | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> PaginatedResponse:
        """分页查询资讯列表。

        Args:
            page: 页码，从 1 开始。
            page_size: 每页数量。
            info_type: 按资讯类型筛选（news/report/social_media 等）。
            source: 按来源筛选。
            status: 按处理状态筛选。

        Returns:
            分页响应，包含 items、total、page、page_size。
        """
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if info_type:
            params["info_type"] = info_type
        if source:
            params["source"] = source
        if status:
            params["status"] = status
        resp = await self._request("GET", "/information/", params=params)
        return PaginatedResponse(**resp)

    @concurrency_limit("query")
    async def list_iter(
        self,
        *,
        page_size: int = 100,
        info_type: str | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部资讯，自动翻页，适用于大批量导出。

        Args:
            page_size: 每页大小。
            info_type: 按资讯类型筛选。
            source: 按来源筛选。
            status: 按处理状态筛选。

        Yields:
            单条资讯的 dict。
        """
        params: dict[str, Any] = {}
        if info_type:
            params["info_type"] = info_type
        if source:
            params["source"] = source
        if status:
            params["status"] = status
        async for item in self._paginate("/information/", params, page_size):
            yield item

    @concurrency_limit("insert")
    async def check_duplicate(self, data: DedupCheckRequest) -> DedupCheckResponse:
        """检查一条资讯是否与已有资讯重复。

        Args:
            data: 待检查的资讯内容（标题+正文哈希）。

        Returns:
            去重检查结果，包含是否重复及相似资讯列表。
        """
        resp = await self._request("POST", "/information/check-duplicate", json=data.model_dump(mode="json"))
        return DedupCheckResponse(**resp)

    @concurrency_limit("insert")
    async def merge(self, data: InformationMergeRequest) -> InformationMergeResponse:
        """合并两条资讯，将重复资讯合并为一条。

        Args:
            data: 合并请求（源资讯 ID + 目标资讯 ID）。

        Returns:
            合并后的资讯。
        """
        resp = await self._request("POST", "/information/merge", json=data.model_dump(mode="json"))
        return InformationMergeResponse(**resp)

    @concurrency_limit("insert")
    async def extract_entities(self, info_id: str | UUID, data: EntityExtractRequest) -> list[dict]:
        """对指定资讯执行实体抽取。

        Args:
            info_id: 资讯 ID。
            data: 抽取配置（模型、抽取类型等）。

        Returns:
            抽取到的实体列表。
        """
        return await self._request("POST", f"/information/{info_id}/extract-entities", json=data.model_dump(mode="json"))

    @concurrency_limit("query")
    async def get_entities(self, info_id: str | UUID) -> list[dict]:
        """获取某条资讯已关联的实体列表。

        Args:
            info_id: 资讯 ID。

        Returns:
            关联的实体列表。
        """
        return await self._request("GET", f"/information/{info_id}/entities")

    @concurrency_limit("insert")
    async def batch_update_importance(self, data: BatchUpdateImportanceRequest) -> BatchUpdateImportanceResponse:
        """批量更新资讯的重要性评分。

        Args:
            data: 包含 scores 字典，key 为 info_id，value 为 importance_score。

        Returns:
            更新结果，包含成功更新的行数。
        """
        resp = await self._request("PUT", "/information/importance", json=data.model_dump(mode="json"))
        return BatchUpdateImportanceResponse(**resp)