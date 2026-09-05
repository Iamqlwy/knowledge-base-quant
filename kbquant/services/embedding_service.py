"""SiliconFlow embedding service via raw HTTP.

Batches individual requests into multi-text API calls (max 50 texts/batch).
Client-side hash-routed sharding, per-shard cache with inflight dedup.
"""
from __future__ import annotations

import asyncio
import os as _os
import time

import httpx

from kbquant.config import settings

# ---------------------------------------------------------------------------
# Per-worker RPM rate limiter
# ---------------------------------------------------------------------------
# 2000 RPM total, split across uvicorn workers. Each worker gets an equal
# share so the aggregate rate stays under the API limit.
_RPM_PER_WORKER = settings.embedding_rpm / max(1, settings.uvicorn_workers)
_RATE_INTERVAL = 60.0 / _RPM_PER_WORKER if _RPM_PER_WORKER > 0 else 0.0

_last_call: float = 0.0
_rate_lock = asyncio.Lock()


async def _acquire_rate() -> None:
    if _RATE_INTERVAL <= 0:
        return
    global _last_call
    async with _rate_lock:
        now = time.monotonic()
        wait = _last_call + _RATE_INTERVAL - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.monotonic()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
_client: httpx.AsyncClient | None = None
_MAX_RETRIES = 5

# ---------------------------------------------------------------------------
# Auto-batching: multi-worker parallel consumers (4x), hash-routed,
# zero-wait drain after first item, pre-allocated zero vector.
# ---------------------------------------------------------------------------
_BATCH_SIZE = settings.embedding_batch_size
_BATCH_WAIT = max(0.005, settings.embedding_batch_wait * 0.5)
_BATCH_WORKER_COUNT = settings.embedding_batch_worker_count
_BatchItem = tuple[str, asyncio.Future]
_batch_queues: list[asyncio.Queue[_BatchItem]] = []
_batch_tasks: list[asyncio.Task] = []
_batch_api_tasks: set[asyncio.Task] = set()

# Pre-allocated zero vector
_ZERO_VECTOR: list[float] = []

# Sharded embedding cache
_NUM_SHARDS = max(1, min(256, _os.cpu_count() or 16))
_PER_SHARD_MAXSIZE = max(64, settings.embedding_cache_maxsize // _NUM_SHARDS)


class _EmbeddingShard:
    __slots__ = ("lock", "cache", "in_flight")

    def __init__(self):
        self.lock = asyncio.Lock()
        self.cache: dict[str, list[float]] = {}
        self.in_flight: dict[str, asyncio.Future] = {}

    def clear(self):
        self.cache.clear()
        for fut in self.in_flight.values():
            if not fut.done():
                fut.cancel()
        self.in_flight.clear()


_shards = [_EmbeddingShard() for _ in range(_NUM_SHARDS)]


def _get_shard(content: str):
    return _shards[hash(content) % _NUM_SHARDS]


def _init_zero_vector() -> None:
    global _ZERO_VECTOR
    if not _ZERO_VECTOR:
        _ZERO_VECTOR = [0.0] * settings.embedding_dimension


def _get_batch_queue_for(text: str) -> asyncio.Queue[_BatchItem]:
    global _batch_queues, _batch_tasks
    if not _batch_queues:
        _batch_queues = [asyncio.Queue() for _ in range(_BATCH_WORKER_COUNT)]
        for i in range(_BATCH_WORKER_COUNT):
            _batch_tasks.append(asyncio.create_task(_batch_worker(i)))
    idx = hash(text) % _BATCH_WORKER_COUNT
    return _batch_queues[idx]


async def _batch_worker(worker_id: int) -> None:
    q = _batch_queues[worker_id]
    while True:
        batch: list[_BatchItem] = []

        # Block until first item arrives (no busy-wait timeout)
        text, fut = await q.get()
        batch.append((text, fut))

        # Drain any additional items that arrived during the first API call
        while len(batch) < _BATCH_SIZE:
            try:
                text, fut = q.get_nowait()
                batch.append((text, fut))
            except asyncio.QueueEmpty:
                break

        task = asyncio.create_task(_dispatch_batch(batch))
        _batch_api_tasks.add(task)
        task.add_done_callback(_batch_api_tasks.discard)


async def _dispatch_batch(batch: list[_BatchItem]) -> None:
    texts = [item[0] for item in batch]
    try:
        vectors = await _call_embedding_api(texts)
        for (_, fut), vec in zip(batch, vectors):
            if not fut.done():
                fut.set_result(vec)
    except Exception as exc:
        for _, fut in batch:
            if not fut.done():
                fut.set_exception(exc)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=50,
                keepalive_expiry=10.0,
            ),
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
    return _client


async def _call_embedding_api(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    url = f"{settings.embedding_base_url}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.embedding_model,
        "input": texts,
        "dimensions": settings.embedding_dimension,
        "encoding_format": "float",
    }
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            await _acquire_rate()
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"Server error {resp.status_code}: {resp.text}",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            data = resp.json()
            return [d["embedding"] for d in data["data"]]
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


async def get_text_embedding(text: str) -> list[float]:
    content = str(text or "").strip()
    if not content:
        _init_zero_vector()
        return _ZERO_VECTOR

    shard = _get_shard(content)

    async with shard.lock:
        if content in shard.cache:
            shard.cache[content] = shard.cache.pop(content)
            return shard.cache[content]

        if content in shard.in_flight:
            fut = shard.in_flight[content]
            is_creator = False
        else:
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            shard.in_flight[content] = fut
            is_creator = True

    if is_creator:
        try:
            _init_zero_vector()
            q = _get_batch_queue_for(content)
            await q.put((content, fut))
        except Exception:
            async with shard.lock:
                shard.in_flight.pop(content, None)
            fut.cancel()
            raise

    try:
        vector = await fut
    except asyncio.CancelledError:
        async with shard.lock:
            shard.in_flight.pop(content, None)
        raise
    except Exception:
        if is_creator:
            async with shard.lock:
                shard.in_flight.pop(content, None)
        raise

    async with shard.lock:
        if is_creator and shard.cache.get(content) is None:
            if len(shard.cache) >= _PER_SHARD_MAXSIZE:
                shard.cache.pop(next(iter(shard.cache)))
            shard.cache[content] = vector
        shard.in_flight.pop(content, None)
    return vector


async def clear_embedding_cache() -> None:
    for s in _shards:
        async with s.lock:
            s.cache.clear()
            for fut in s.in_flight.values():
                if not fut.done():
                    fut.cancel()
            s.in_flight.clear()


class EmbeddingService:
    def __init__(self):
        pass

    @property
    def dimension(self) -> int:
        return settings.embedding_dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Bulk embed — merges with the batching queue."""
        _init_zero_vector()
        tasks = []
        for t in texts:
            content = str(t or "").strip()
            if not content:
                tasks.append(None)
            else:
                tasks.append(get_text_embedding(content))
        results = await asyncio.gather(*(t for t in tasks if t is not None), return_exceptions=False)
        result_iter = iter(results)
        return [next(result_iter) if t is not None else _ZERO_VECTOR for t in tasks]

    async def embed_text(self, text: str) -> list[float]:
        return await get_text_embedding(text)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return await self.embed(texts)


embedding_service = EmbeddingService()


async def generate_embedding_for(text: str) -> list[float]:
    return await get_text_embedding(text)
