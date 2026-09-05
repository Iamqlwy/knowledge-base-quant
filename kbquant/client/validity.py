from __future__ import annotations

from uuid import UUID

from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas.validity import (
    TimeValidityCreate,
    TimeValidityResponse,
    ValidityCheckResponse,
    ValidityExpireRequest,
    ValidityExtendRequest,
)


class ValidityClient(BaseClient):
    """时效性客户端，管理实体/关系/状态的时间有效期。"""

    @concurrency_limit("insert")
    async def create(self, data: TimeValidityCreate) -> TimeValidityResponse:
        """创建一条时效性记录。

        Args:
            data: 时效性数据（目标类型、目标 ID、有效起止时间等）。

        Returns:
            创建成功的时效性记录。
        """
        resp = await self._request("POST", "/validity/", json=data.model_dump(mode="json"))
        return TimeValidityResponse(**resp)

    @concurrency_limit("query")
    async def list(
        self, *, target_type: str | None = None, expired: bool | None = None,
    ) -> list[TimeValidityResponse]:
        """查询时效性记录列表。

        Args:
            target_type: 按目标类型筛选（entity/relationship/state 等）。
            expired: 是否已过期。

        Returns:
            时效性记录列表。
        """
        params: dict = {}
        if target_type:
            params["target_type"] = target_type
        if expired is not None:
            params["expired"] = expired
        resp = await self._request("GET", "/validity/", params=params)
        return [TimeValidityResponse(**r) for r in resp]

    @concurrency_limit("insert")
    async def expire(self, validity_id: str | UUID, data: ValidityExpireRequest) -> TimeValidityResponse:
        """将一条时效性记录标记为过期。

        Args:
            validity_id: 时效性记录 ID。
            data: 过期原因等信息。

        Returns:
            更新后的时效性记录。
        """
        resp = await self._request(
            "PUT", f"/validity/{validity_id}/expire", json=data.model_dump(mode="json"),
        )
        return TimeValidityResponse(**resp)

    @concurrency_limit("insert")
    async def extend(self, validity_id: str | UUID, data: ValidityExtendRequest) -> TimeValidityResponse:
        """延长一条时效性记录的有效期。

        Args:
            validity_id: 时效性记录 ID。
            data: 新的截止时间。

        Returns:
            更新后的时效性记录。
        """
        resp = await self._request(
            "PUT", f"/validity/{validity_id}/extend", json=data.model_dump(mode="json"),
        )
        return TimeValidityResponse(**resp)

    @concurrency_limit("query")
    async def check(self, *, target_type: str, target_id: str) -> ValidityCheckResponse:
        """检查某个目标当前的时效性状态。

        Args:
            target_type: 目标类型。
            target_id: 目标 ID。

        Returns:
            时效性检查结果（是否有效、过期时间等）。
        """
        resp = await self._request(
            "GET", "/validity/check",
            params={"target_type": target_type, "target_id": target_id},
        )
        return ValidityCheckResponse(**resp)