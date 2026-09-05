"""请求级并发控制中间件"""
from __future__ import annotations
import asyncio, logging
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse
from kbquant.config import settings

logger = logging.getLogger(__name__)

# ── Global request admission control (per uvicorn worker) ──
# DB sessions are now lazy — opened on demand and closed immediately.
# So the pool is no longer held per-request.  This middleware protects
# against memory/CPU overload rather than pool exhaustion.
_admission_semaphore: asyncio.Semaphore | None = None
_admission_rejected: int = 0
_admission_total: int = 0
_admission_depth: int = 0

def _get_admission_limit() -> int:
    # With lazy sessions, DB connections are held for ~5-50ms per query
    # instead of the entire request.  The pool is no longer the bottleneck.
    # Limit by reasonable concurrency per worker (CPU/memory) rather than pool size.
    return max(1, getattr(settings, 'admission_max_concurrent', 500))

def _get_admission_semaphore() -> asyncio.Semaphore:
    global _admission_semaphore
    if _admission_semaphore is None:
        limit = _get_admission_limit()
        _admission_semaphore = asyncio.Semaphore(limit)
        logger.info("请求准入控制: max_concurrent=%d (write=%d+%d, read=%d+%d)",
                    limit,
                    settings.database_pool_size, settings.database_max_overflow,
                    settings.database_read_pool_size, settings.database_read_max_overflow)
    return _admission_semaphore

class RequestAdmissionMiddleware:
    """全局请求准入控制 — 纯 ASGI 中间件，避免 BaseHTTPMiddleware 的信号量释放 bug"""
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Exclude non-HTTP requests (lifespan, websocket, etc.)
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Exclude health/metrics from admission control
        path = scope.get("path", "")
        if path in ("/health", "/metrics"):
            await self.app(scope, receive, send)
            return

        sem = _get_admission_semaphore()
        global _admission_total, _admission_depth, _admission_rejected
        _admission_total += 1
        _admission_depth += 1

        try:
            async with asyncio.timeout(settings.admission_timeout):
                await sem.acquire()
        except asyncio.TimeoutError:
            _admission_rejected += 1
            _admission_depth -= 1
            response = JSONResponse(
                status_code=503,
                content={"detail": "服务繁忙，请稍后重试", "error_code": "SERVER_BUSY"},
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return

        _admission_depth -= 1
        try:
            await self.app(scope, receive, send)
        finally:
            sem.release()


# ── Search-specific concurrency limiter ──
_search_semaphore: asyncio.Semaphore | None = None
_search_queue_depth = 0
_search_queue_total = 0
_search_queue_timeouts = 0

def _get_search_semaphore() -> asyncio.Semaphore:
    global _search_semaphore
    if _search_semaphore is None:
        limit = max(1, settings.search_max_concurrent)
        _search_semaphore = asyncio.Semaphore(limit)
        logger.info("搜索并发限制: max_concurrent=%d", limit)
    return _search_semaphore

def get_search_queue_metrics() -> dict:
    return {
        "max_concurrent": settings.search_max_concurrent,
        "current_queue_depth": _search_queue_depth,
        "total_queued": _search_queue_total,
        "total_timeouts": _search_queue_timeouts,
    }

def get_admission_metrics() -> dict:
    return {
        "max_concurrent": _get_admission_limit(),
        "current_depth": _admission_depth,
        "total_requests": _admission_total,
        "total_rejected": _admission_rejected,
    }

class SearchConcurrencyMiddleware:
    """搜索专用并发限制 — 纯 ASGI 中间件"""
    def __init__(self, app: ASGIApp, path_prefixes: tuple[str, ...]) -> None:
        self.app = app
        self._path_prefixes = path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not any(path.startswith(prefix) for prefix in self._path_prefixes):
            await self.app(scope, receive, send)
            return

        sem = _get_search_semaphore()
        global _search_queue_depth, _search_queue_total, _search_queue_timeouts
        _search_queue_total += 1
        _search_queue_depth += 1

        try:
            async with asyncio.timeout(settings.search_queue_timeout):
                await sem.acquire()
                _search_queue_depth -= 1
                try:
                    await self.app(scope, receive, send)
                finally:
                    sem.release()
                return
        except asyncio.TimeoutError:
            _search_queue_timeouts += 1
            _search_queue_depth -= 1
            response = JSONResponse(
                status_code=503,
                content={"detail": "服务繁忙，请稍后重试", "error_code": "SEARCH_QUEUE_FULL"},
            )
            await response(scope, receive, send)

