from __future__ import annotations

import asyncio
import contextvars
import os
import re
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from random import random
from typing import AsyncIterator


_HELD_LIMITS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "kbquant_held_global_limits",
    default=(),
)


class FileGlobalSemaphore:
    """Small cross-process semaphore backed by atomic lock files."""

    def __init__(
        self,
        name: str,
        limit: int,
        *,
        timeout: float = 300.0,
        stale_after: float = 3600.0,
        poll_interval: float = 0.02,
    ):
        self.name = _safe_name(name)
        self.limit = max(1, int(limit))
        self.timeout = timeout
        self.stale_after = stale_after
        self.poll_interval = poll_interval
        self.dir = Path(tempfile.gettempdir()) / "kbquant_limiters" / self.name
        self.dir.mkdir(parents=True, exist_ok=True)

    async def acquire(self) -> Path:
        deadline = time.monotonic() + self.timeout if self.timeout > 0 else None
        while True:
            slot = self._try_acquire_once()
            if slot is not None:
                return slot
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"global limiter timeout: {self.name}")
            await asyncio.sleep(self.poll_interval + random() * self.poll_interval)

    def release(self, slot: Path) -> None:
        try:
            slot.unlink()
        except FileNotFoundError:
            pass

    def _try_acquire_once(self) -> Path | None:
        now = time.time()
        start_idx = int(random() * self.limit)
        for offset in range(self.limit):
            idx = (start_idx + offset) % self.limit
            slot = self.dir / f"{idx}.lock"
            try:
                fd = os.open(str(slot), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                self._remove_stale(slot, now)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{os.getpid()} {now}\n")
            return slot
        return None

    def _remove_stale(self, slot: Path, now: float) -> None:
        if self.stale_after <= 0:
            return
        try:
            age = now - slot.stat().st_mtime
        except FileNotFoundError:
            return
        if age < self.stale_after:
            return
        try:
            slot.unlink()
        except FileNotFoundError:
            pass


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return cleaned or "default"


@asynccontextmanager
async def global_limit(
    name: str,
    limit: int,
    *,
    timeout: float = 300.0,
    stale_after: float = 3600.0,
) -> AsyncIterator[None]:
    held = _HELD_LIMITS.get()
    safe_name = _safe_name(name)
    if safe_name in held:
        yield
        return

    limiter = FileGlobalSemaphore(
        safe_name,
        limit,
        timeout=timeout,
        stale_after=stale_after,
    )
    slot = await limiter.acquire()
    token = _HELD_LIMITS.set((*held, safe_name))
    try:
        yield
    finally:
        _HELD_LIMITS.reset(token)
        limiter.release(slot)
