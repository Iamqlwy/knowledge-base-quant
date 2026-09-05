from __future__ import annotations
from kbquant.client._limiter import concurrency_limit

from datetime import datetime
from urllib.parse import quote

from kbquant.client._limiter import concurrency_limit
from kbquant.client._base import BaseClient
from kbquant.schemas.preference import (
    IndustryCognitionAppend,
    IndustryCognitionAppendResponse,
    IndustryCognitionResponse,
    IndustryCognitionSectorsResponse,
    MarketCognitionAppend,
    MarketCognitionAppendResponse,
    MarketCognitionResponse,
    StructuredPreferencesResponse,
    StructuredPreferencesUpdate,
    SuggestionsPayload,
    SuggestionsResponse,
)

class PreferenceClient(BaseClient):
    @concurrency_limit("query")
    async def get_all_sectors(self) -> IndustryCognitionSectorsResponse:
        resp = await self._request("GET", "/preferences/sectors")
        return IndustryCognitionSectorsResponse(**resp)

    @concurrency_limit("query")
    async def get_industry_cognition(self, sector: str) -> IndustryCognitionResponse:
        resp = await self._request("GET", f"/preferences/{quote(sector, safe='')}/cognition")
        return IndustryCognitionResponse(**resp)

    @concurrency_limit("insert")
    async def append_industry_cognition(
        self, sector: str, text: str, custom_time: datetime | None = None,
    ) -> IndustryCognitionAppendResponse:
        data = IndustryCognitionAppend(text=text, custom_time=custom_time)
        resp = await self._request(
            "POST", f"/preferences/{quote(sector, safe='')}/cognition",
            json=data.model_dump(mode="json"),
        )
        return IndustryCognitionAppendResponse(**resp)

    @concurrency_limit("query")
    async def get_structured(self) -> StructuredPreferencesResponse:
        resp = await self._request("GET", "/preferences/structured")
        return StructuredPreferencesResponse(**resp)

    @concurrency_limit("insert")
    async def update_structured(
        self, data: StructuredPreferencesUpdate
    ) -> StructuredPreferencesResponse:
        resp = await self._request(
            "PUT", "/preferences/structured", json=data.model_dump(mode="json", exclude_none=True)
        )
        return StructuredPreferencesResponse(**resp)

    @concurrency_limit("insert")
    async def apply_suggestions(self, data: SuggestionsPayload) -> SuggestionsResponse:
        resp = await self._request(
            "POST", "/preferences/suggestions", json=data.model_dump(mode="json")
        )
        return SuggestionsResponse(**resp)

    @concurrency_limit("query")
    async def get_market_cognition(self) -> MarketCognitionResponse:
        resp = await self._request("GET", "/preferences/market/cognition")
        return MarketCognitionResponse(**resp)

    @concurrency_limit("insert")
    async def append_market_cognition(self, text: str, custom_time: datetime | None = None) -> MarketCognitionAppendResponse:
        data = MarketCognitionAppend(text=text, custom_time=custom_time)
        resp = await self._request(
            "POST", "/preferences/market/cognition",
            json=data.model_dump(mode="json"),
        )
        return MarketCognitionAppendResponse(**resp)
