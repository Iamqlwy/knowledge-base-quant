from kbquant.client._base import BaseClient
from kbquant.client._limiter import concurrency_limit
from kbquant.schemas.macro_report import (
    MacroReportHistoryResponse,
    MacroReportResponse,
    MacroReportUpdate,
)


class MacroReportClient(BaseClient):
    @concurrency_limit("query")
    async def get_current(self) -> MacroReportResponse:
        resp = await self._request("GET", "/macro-report/current")
        return MacroReportResponse(**resp)

    @concurrency_limit("insert")
    async def update(self, data: MacroReportUpdate) -> MacroReportResponse:
        resp = await self._request(
            "PUT", "/macro-report", json=data.model_dump(mode="json")
        )
        return MacroReportResponse(**resp)

    @concurrency_limit("query")
    async def get_history(self, limit: int = 50) -> MacroReportHistoryResponse:
        resp = await self._request("GET", "/macro-report/history", params={"limit": limit})
        return MacroReportHistoryResponse(**resp)