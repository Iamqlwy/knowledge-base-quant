from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Callable

logger = logging.getLogger("kbquant.client.limiter")

# Default concurrency limits per tier
_DEFAULT_LIMITS: dict[str, int] = {
    "query": 200,
    "insert": 200,
    "search": 50,
}

# Attribute name stored on decorated functions so the wrapper can read the tier at runtime.
_TIER_ATTR = "_quantclient_concurrency_tier"


class ClientConcurrencyLimiter:
    """Holds one asyncio.Semaphore per concurrency tier.

    Tiers are independent: exhausting "query" slots does not block "insert" or "search".
    """

    def __init__(
        self,
        enabled: bool = True,
        limits: dict[str, int] | None = None,
    ) -> None:
        self._enabled = enabled
        merged = dict(_DEFAULT_LIMITS)
        if limits:
            merged.update(limits)
        self._limits = merged
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        for tier, limit in self._limits.items():
            if limit < 1:
                raise ValueError(f"Limit for tier '{tier}' must be >= 1, got {limit}")
            self._semaphores[tier] = asyncio.Semaphore(limit)
        logger.debug(
            "ClientConcurrencyLimiter created enabled=%s limits=%s",
            self._enabled,
            self._limits,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def limits(self) -> dict[str, int]:
        return dict(self._limits)

    def get_semaphore(self, tier: str) -> asyncio.Semaphore | None:
        """Return the semaphore for *tier*, or None when disabled / tier unknown."""
        if not self._enabled:
            return None
        return self._semaphores.get(tier)


def concurrency_limit(tier: str):
    """Decorator that wraps an async client method with per-tier concurrency control.

    Usage::

        class SearchClient(BaseClient):
            @concurrency_limit("search")
            async def search(self, data: SearchRequest) -> SearchResponse:
                ...

    The semaphore is acquired before the method body and released after it returns
    (or raises).  No semaphore is held when `self._limiter` is None or disabled,
    making the decorator a no-op in legacy usage.
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            limiter: ClientConcurrencyLimiter | None = getattr(self, "_limiter", None)
            if limiter is not None and limiter.enabled:
                sem = limiter.get_semaphore(tier)
                if sem is not None:
                    async with sem:
                        return await func(self, *args, **kwargs)
            # No limiter, disabled, or unknown tier — pass through.
            return await func(self, *args, **kwargs)

        setattr(wrapper, _TIER_ATTR, tier)
        return wrapper

    return decorator