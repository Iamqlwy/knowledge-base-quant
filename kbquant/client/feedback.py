from __future__ import annotations

from typing import AsyncGenerator
from uuid import UUID

from kbquant.client._limiter import concurrency_limit
from kbquant.client._base import BaseClient
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.feedback import FeedbackCreate, FeedbackResponse
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.feedback import FeedbackCreate, FeedbackResponse

class FeedbackClient(BaseClient):
    """反馈客户端，管理交易决策的反馈评估和经验教训。"""

    @concurrency_limit("insert")
    async def create(self, data: FeedbackCreate) -> FeedbackResponse:
        """提交一条反馈。

        Args:
            data: 反馈数据（关联分析、判断是否正确、教训等）。

        Returns:
            创建成功的反馈记录。
        """
        resp = await self._request("POST", "/feedback/", json=data.model_dump(mode="json"))
        return FeedbackResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self,
        *,
        judgment_correct: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse:
        """分页查询反馈记录。

        Args:
            judgment_correct: 按判断正确性筛选。
            page: 页码。
            page_size: 每页数量。

        Returns:
            分页响应。
        """
        params: dict = {"page": page, "page_size": page_size}
        if judgment_correct is not None:
            params["judgment_correct"] = judgment_correct
        resp = await self._request("GET", "/feedback/", params=params)
        return PaginatedResponse(**resp)

    @concurrency_limit("query")
    async def list_iter(
        self,
        *,
        page_size: int = 100,
        judgment_correct: bool | None = None,
    ) -> AsyncGenerator[dict, None]:
        """异步遍历全部反馈记录，自动翻页。

        Args:
            page_size: 每页大小。
            judgment_correct: 按判断正确性筛选。

        Yields:
            单条反馈记录的 dict。
        """
        params: dict = {}
        if judgment_correct is not None:
            params["judgment_correct"] = judgment_correct
        async for item in self._paginate("/feedback/", params, page_size):
            yield item

    @concurrency_limit("query")
    async def get_lessons(self, *, search_text: str | None = None) -> list[dict]:
        """查询经验教训。

        Args:
            search_text: 按文本搜索相关教训。

        Returns:
            经验教训列表。
        """
        params: dict = {}
        if search_text:
            params["search_text"] = search_text
        return await self._request("GET", "/feedback/lessons", params=params)

    @concurrency_limit("query")
    async def get(self, feedback_id: str | UUID) -> FeedbackResponse:
        """按 ID 获取反馈详情。

        Args:
            feedback_id: 反馈 ID。

        Returns:
            反馈详情。
        """
        resp = await self._request("GET", f"/feedback/{feedback_id}")
        return FeedbackResponse(**resp)

    @concurrency_limit("query")
    async def get_many(self, feedback_ids: list[str | UUID]) -> list[FeedbackResponse]:
        resp = await self._request("POST", "/feedback/batch", json={"ids": [str(i) for i in feedback_ids]})
        return [FeedbackResponse(**r) for r in resp]
