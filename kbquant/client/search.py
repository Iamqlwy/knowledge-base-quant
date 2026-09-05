from __future__ import annotations

from kbquant.client._limiter import concurrency_limit
from kbquant.client._base import BaseClient
from kbquant.schemas.search import (
    FetchByIdsRequest,
    FetchByIdsResponse,
    SearchRequest,
    SearchResponse,
)
 
class SearchClient(BaseClient):
    """搜索客户端，提供统一搜索和按 ID 批量查询。"""

    @concurrency_limit("search")
    async def search(self, data: SearchRequest) -> SearchResponse:
        """统一搜索，通过 mode 控制搜索策略。

        Args:
            data: 搜索请求（查询文本、mode、过滤条件、返回数量等）。

        Returns:
            搜索结果。
        """
        resp = await self._request("POST", "/search", json=data.model_dump(mode="json"))
        return SearchResponse(**resp)

    @concurrency_limit("query")
    async def fetch_by_ids(self, data: FetchByIdsRequest) -> FetchByIdsResponse:
        """根据表名和 ID 列表批量查询数据。

        Args:
            data: 请求体，table_ids 为 {表名: [id列表]} 的字典。

        Returns:
            按表名分组的数据字典。
        """
        resp = await self._request("POST", "/search/fetch-by-ids", json=data.model_dump(mode="json"))
        return FetchByIdsResponse(**resp)
