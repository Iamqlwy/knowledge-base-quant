from __future__ import annotations

from typing import Any, AsyncGenerator, TYPE_CHECKING

import httpx

from kbquant.schemas import ErrorResponse

if TYPE_CHECKING:
    from kbquant.client._limiter import ClientConcurrencyLimiter


class QuantClientError(Exception):
    """客户端基础异常，所有自定义错误的父类。"""

    def __init__(self, detail: str, error_code: str | None = None):
        self.detail = detail
        self.error_code = error_code
        super().__init__(detail)


class QuantClientHTTPError(QuantClientError):
    """HTTP 响应错误（4xx/5xx），携带 HTTP 状态码。"""

    def __init__(self, status_code: int, detail: str, error_code: str | None = None):
        self.status_code = status_code
        super().__init__(detail, error_code)


class QuantClientAuthError(QuantClientHTTPError):
    """认证失败（401），API Key 无效或未提供。"""

    def __init__(self, detail: str = "Invalid API key"):
        super().__init__(401, detail, "unauthorized")


class QuantClientNotFoundError(QuantClientHTTPError):
    """资源不存在（404）。"""

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(404, detail, "not_found")


class QuantClientConnectionError(QuantClientError):
    """网络连接失败，无法到达服务端。"""

    def __init__(self, detail: str):
        super().__init__(detail, "connection_error")


_GATEWAY_MSGS: dict[int, str] = {502: "上游服务无响应（网关超时）", 504: "上游服务响应超时（网关超时）", 503: "服务暂时不可用"}

def _parse_error(status_code: int, body: dict | str) -> QuantClientHTTPError:
    if isinstance(body, dict):
        try:
            err = ErrorResponse(**body)
            detail = err.detail
            error_code = err.error_code
        except Exception:
            detail = body.get("detail", str(body))
            error_code = body.get("error_code")
    else:
        detail = str(body)
        error_code = None
        if not detail and status_code in _GATEWAY_MSGS:
            detail = _GATEWAY_MSGS[status_code]
        elif not detail:
            detail = f"服务端返回 HTTP {status_code}，无详细错误信息"
            error_code = "empty_error_body"

    if status_code == 401:
        return QuantClientAuthError(detail)
    if status_code == 404:
        return QuantClientNotFoundError(detail)
    return QuantClientHTTPError(status_code, detail, error_code)


class BaseClient:
    def __init__(self, session: httpx.AsyncClient, api_key: str | None):
        self._session = session
        self._limiter: ClientConcurrencyLimiter | None = None
        self._api_key = api_key

    @property
    def http(self) -> httpx.AsyncClient:
        return self._session

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._api_key:
            h["X-API-Key"] = self._api_key
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> Any:
        url = f"/api/v1{path}"
        try:
            resp = await self._session.request(
                method, url, json=json, params=params, headers=self._headers(), timeout=timeout,
            )
        except httpx.PoolTimeout as e:
            raise QuantClientConnectionError(
                "HTTP client connection pool exhausted; increase max_connections or reduce client concurrency"
            ) from e
        except httpx.TimeoutException as e:
            raise QuantClientConnectionError(f"HTTP request timed out: {e.__class__.__name__}") from e
        except httpx.ConnectError as e:
            detail = str(e) or e.__class__.__name__
            raise QuantClientConnectionError(f"HTTP connection failed: {detail}") from e
        except httpx.HTTPError as e:
            detail = str(e) or e.__class__.__name__
            raise QuantClientConnectionError(detail) from e

        if resp.is_success:
            return resp.json()

        try:
            body = resp.json()
        except Exception:
            body = resp.text
        raise _parse_error(resp.status_code, body)

    async def _paginate(
        self,
        path: str,
        params: dict | None = None,
        page_size: int = 100,
    ) -> AsyncGenerator[dict, None]:
        params = dict(params or {})
        page = 1
        while True:
            params["page"] = page
            params["page_size"] = page_size
            data = await self._request("GET", path, params=params)
            items = data.get("items", [])
            total = data.get("total", 0)
            for item in items:
                yield item
            if len(items) < page_size or page * page_size >= total:
                break
            page += 1