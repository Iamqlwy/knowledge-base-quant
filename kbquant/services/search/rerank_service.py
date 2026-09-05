"""阶段6: Rerank — 多厂商 fallback 重排序。

Fallback 链: SiliconFlow (RPM 2000) → DashScope (RPM 5400)
每个 provider 的 RPM 限制按 uvicorn worker 数量均分，合起来不超 API 限制。
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass

import httpx

from kbquant.config import settings
from kbquant.models.search_candidate import Candidate, SearchContext

logger = logging.getLogger(__name__)

_RERANK_TOP_N = 50
_DEFAULT_TIMEOUT = 5.0
_MAX_RETRIES = 3
_MAX_QUEUE_DEPTH = 500  # match search_max_concurrent * uvicorn_workers
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# Per-worker RPM share
_NUM_WORKERS = max(1, settings.uvicorn_workers)


class _RateLimiter:
    """Per-worker token-bucket rate limiter using async Semaphore.

    Instead of serialising calls through a lock+sleep (which kills throughput
    under contention), we use a Semaphore that refills one token per interval.
    This allows bursts while staying under the long-term RPM limit.
    """

    def __init__(self, total_rpm: int) -> None:
        rpm = total_rpm / _NUM_WORKERS
        self._interval = 60.0 / rpm if rpm > 0 else 0.0
        self._sem = asyncio.Semaphore(max(1, int(rpm)))
        self._refill_task: asyncio.Task | None = None

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        await self._sem.acquire()
        # Schedule a single token refill after the interval
        if self._refill_task is None or self._refill_task.done():
            self._refill_task = asyncio.create_task(self._refill())

    async def _refill(self) -> None:
        await asyncio.sleep(self._interval)
        try:
            self._sem.release()
        except ValueError:
            pass  # semaphore already at max


@dataclass
class RerankProvider:
    name: str
    api_url: str
    api_key: str
    model: str
    total_rpm: int  # total across all workers
    priority: int  # lower = higher priority

    def __post_init__(self):
        self.limiter = _RateLimiter(self.total_rpm)

    def build_payload(self, query: str, documents: list[str]) -> dict:
        raise NotImplementedError

    def parse_response(self, data: dict, doc_count: int) -> list[float]:
        raise NotImplementedError


class SiliconFlowProvider(RerankProvider):
    def build_payload(self, query: str, documents: list[str]) -> dict:
        return {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
            "return_documents": False,
        }

    def parse_response(self, data: dict, doc_count: int) -> list[float]:
        results = data.get("results", [])
        score_map: dict[int, float] = {}
        for item in results:
            idx = item.get("index", -1)
            score = item.get("relevance_score", 0.0)
            if idx >= 0:
                score_map[idx] = float(score)
        return [score_map.get(i, 0.0) for i in range(doc_count)]


class DashScopeProvider(RerankProvider):
    def build_payload(self, query: str, documents: list[str]) -> dict:
        return {
            "model": self.model,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "return_documents": False,
                "top_n": len(documents),
            },
        }

    def parse_response(self, data: dict, doc_count: int) -> list[float]:
        results = data.get("output", {}).get("results", [])
        score_map: dict[int, float] = {}
        for item in results:
            idx = item.get("index", -1)
            score = item.get("relevance_score", 0.0)
            if idx >= 0:
                score_map[idx] = float(score)
        return [score_map.get(i, 0.0) for i in range(doc_count)]


_providers_cache: list[RerankProvider] | None = None


def _build_providers() -> list[RerankProvider]:
    global _providers_cache
    if _providers_cache is not None:
        return _providers_cache

    providers: list[RerankProvider] = []

    if settings.siliconflow_api_key:
        providers.append(SiliconFlowProvider(
            name="siliconflow",
            api_url="https://api.siliconflow.cn/v1/rerank",
            api_key=settings.siliconflow_api_key,
            model="Qwen/Qwen3-Reranker-0.6B",
            total_rpm=2000,
            priority=1,
        ))

    dashscope_key = settings.dashscope_api_key or ""
    if dashscope_key:
        providers.append(DashScopeProvider(
            name="dashscope",
            api_url=settings.rerank_api_url,
            api_key=dashscope_key,
            model=settings.rerank_model,
            total_rpm=5400,
            priority=3,
        ))

    providers.sort(key=lambda p: p.priority)
    _providers_cache = providers
    return providers


class RerankService:
    """阶段6: 对 RRF 融合后的 top-N 候选进行语义重排序。

    Fallback 链: SiliconFlow → Gitee AI → DashScope → 保持原序
    """

    _shared_client: httpx.AsyncClient | None = None
    _client_lock = asyncio.Lock()
    _pending_count: int = 0
    _pending_lock = asyncio.Lock()


    # Text fields searched across ALL candidate sources (raw + es_source)
    # to provide the reranker with the best possible signal for every
    # document type — a general improvement, not a node-specific fix.
    _TEXT_FIELDS = (
        "body", "content", "lessons_learned", "description",
        "state_summary", "core_logic", "name", "node_type",
    )
    def __init__(self):
        self._providers = _build_providers()

    @classmethod
    async def _get_client(cls) -> httpx.AsyncClient:
        if cls._shared_client is None:
            async with cls._client_lock:
                if cls._shared_client is None:
                    cls._shared_client = httpx.AsyncClient(
                        timeout=httpx.Timeout(10.0, connect=3.0),
                        proxy=None,
                        limits=httpx.Limits(max_connections=30, max_keepalive_connections=10, keepalive_expiry=10.0),
                    )
        return cls._shared_client

    async def rerank(
        self,
        query: str,
        candidates: list[Candidate],
        top_n: int = _RERANK_TOP_N,
        ctx: SearchContext | None = None,
    ) -> list[Candidate]:
        if not candidates or not self._providers:
            return candidates

        async with RerankService._pending_lock:
            if RerankService._pending_count >= _MAX_QUEUE_DEPTH:
                logger.warning("rerank queue depth %d >= %d, skipping",
                               RerankService._pending_count, _MAX_QUEUE_DEPTH)
                return candidates
            RerankService._pending_count += 1

        try:
            to_rerank = candidates[:top_n]
            rest = candidates[top_n:]

            documents = []
            for c in to_rerank:
                title = c.title or ""
                # Build text from the candidate's raw row body fields,
                # and ES source fields -- all text-bearing fields across
                # every document type.  Fall back to snippet only when
                # no structured text is available.
                body_parts: list[str] = []

                # 1. Extract from pgvector row (c.raw)
                if c.raw is not None:
                    raw = c.raw
                    for field in self._TEXT_FIELDS:
                        val = raw.get(field, "") if isinstance(raw, dict) else getattr(raw, field, "")
                        if val:
                            body_parts.append(str(val))

                # 2. Extract from ES source (c.es_source)
                if c.es_source:
                    for field in self._TEXT_FIELDS:
                        val = c.es_source.get(field, "")
                        if val and val not in body_parts:
                            body_parts.append(val)

                body = " ".join(body_parts).strip()

                if not body and c.snippet:
                    body = c.snippet
                text = f"{title} {body}"[:512].strip()
                if not text:
                    text = title or "untitled"
                documents.append(text)

            try:
                rerank_scores = await self._call_with_fallback(query, documents)
            except Exception as exc:
                logger.warning("All rerank providers failed(%s), fallback to RRF ordering",
                               type(exc).__name__)
                return candidates

            for c, score in zip(to_rerank, rerank_scores):
                c.reranker_score = round(score, 6)

            to_rerank.sort(key=lambda c: c.reranker_score, reverse=True)

            if ctx is not None:
                ctx.timings["rerank_applied"] = True

            return to_rerank + rest
        finally:
            async with RerankService._pending_lock:
                RerankService._pending_count -= 1

    async def _try_provider(
        self, provider: RerankProvider, query: str, documents: list[str]
    ) -> list[float] | None:
        """Try a single provider. Returns scores on success, None on failure."""
        await provider.limiter.acquire()

        payload = provider.build_payload(query, documents)
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }

        client = await self._get_client()
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.post(provider.api_url, json=payload, headers=headers)

                if resp.status_code in _RETRYABLE_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"Retryable status {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                if resp.status_code == 401:
                    logger.error("Rerank provider %s returned 401 (invalid API key), "
                                 "disabling for this session", provider.name)
                    return None
                if resp.status_code >= 400:
                    body = resp.text[:500]
                    logger.warning("Rerank provider %s error %d: %s",
                                   provider.name, resp.status_code, body)
                    return None

                data = resp.json()
                logger.debug("Rerank [%s] response: %s",
                             provider.name, json.dumps(data, ensure_ascii=False)[:500])

                scores = provider.parse_response(data, len(documents))
                if not scores:
                    logger.warning("Rerank provider %s returned empty results", provider.name)
                    return None

                logger.info("Rerank provider %s succeeded", provider.name)
                return scores

            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 401:
                    return None
                if exc.response is not None and exc.response.status_code not in _RETRYABLE_STATUSES:
                    return None
                wait = min(2 ** attempt, 4)
                logger.debug("Rerank [%s] attempt %d/%d (%s), retry in %ss",
                             provider.name, attempt + 1, _MAX_RETRIES,
                             type(exc).__name__, wait)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(wait)
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                wait = min(2 ** attempt, 4)
                logger.debug("Rerank [%s] attempt %d/%d (%s), retry in %ss",
                             provider.name, attempt + 1, _MAX_RETRIES,
                             type(exc).__name__, wait)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(wait)
            except Exception:
                return None

        logger.warning("Rerank provider %s exhausted retries", provider.name)
        return None

    async def _call_with_fallback(
        self, query: str, documents: list[str]
    ) -> list[float]:
        for provider in self._providers:
            scores = await self._try_provider(provider, query, documents)
            if scores is not None:
                return scores
            logger.info("Rerank provider %s unavailable, falling through to next",
                        provider.name)

        raise Exception("all rerank providers exhausted")

    async def health_check(self) -> bool:
        if not self._providers:
            return False
        scores = await self._try_provider(
            self._providers[0], "health check", ["health check text"]
        )
        return scores is not None and len(scores) > 0
