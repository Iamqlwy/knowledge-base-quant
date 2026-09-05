import asyncio
import hashlib
import json
import logging
import re

import httpx
from openai import AsyncOpenAI

from kbquant.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 并发控制 (模块级，所有 LLMService 实例共享)
# ---------------------------------------------------------------------------
_API_SEMAPHORE = asyncio.Semaphore(settings.llm_max_concurrent)
_MAX_RETRIES = 5

# ---------------------------------------------------------------------------
# 请求去重 / 结果缓存 — 分片缓存，降低锁竞争
# ---------------------------------------------------------------------------
import os as _os
_LLM_NUM_SHARDS = max(1, min(256, _os.cpu_count() or 16))

class _LLMShard:
    __slots__ = ("lock", "cache", "in_flight")
    def __init__(self):
        self.lock = asyncio.Lock()
        self.cache: dict[str, str] = {}
        self.in_flight: dict[str, asyncio.Future] = {}

    def clear(self):
        self.cache.clear()
        self.in_flight.clear()

_llm_shards = [_LLMShard() for _ in range(_LLM_NUM_SHARDS)]
_LLM_PER_SHARD_MAXSIZE = max(64, settings.llm_cache_maxsize // _LLM_NUM_SHARDS)

def _get_llm_shard(key: str) -> _LLMShard:
    return _llm_shards[hash(key) % _LLM_NUM_SHARDS]

def _get_cache_maxsize() -> int:
    return settings.llm_cache_maxsize


def _make_cache_key(model: str, system: str, user: str, temperature: float) -> str:
    raw = f"{model}|{system}|{user}|{temperature}"
    return hashlib.sha256(raw.encode()).hexdigest()


def clear_llm_cache() -> None:
    for s in _llm_shards:
        s.clear()


def _extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"无法从回复中提取 JSON: {text[:200]}")


class LLMService:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self._model = model or getattr(settings, "llm_model", "qwen3.6-flash")
        self._api_key = api_key or settings.deepseek_api_key
        self._base_url = settings.deepseek_base_url
        self._http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=10.0),
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        self.client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=self._http_client,
        )

    async def _call_api(self, messages: list[dict], temperature: float) -> str:
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                async with _API_SEMAPHORE:
                    resp = await self.client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        temperature=temperature,
                        extra_body= {"thinking": {"type": "disabled"}}
                    )
                return resp.choices[0].message.content
            except Exception as exc:
                last_exc = exc
                logger.warning("LLM 调用失败 (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES, exc)
                if attempt < _MAX_RETRIES - 1:
                    # Release semaphore before sleeping so other requests can proceed
                    await asyncio.sleep(2 ** attempt)
        raise last_exc  # type: ignore[misc]

    async def chat_json(self, system_prompt: str, user_message: str,
                        temperature: float = 0.0) -> dict:
        content = await self.chat(system_prompt, user_message, temperature)
        return _extract_json(content)

    async def chat(self, system_prompt: str, user_message: str,
                   temperature: float = 0.0) -> str:
        key = _make_cache_key(self._model, system_prompt, user_message, temperature)
        shard = _get_llm_shard(key)

        async with shard.lock:
            if key in shard.cache:
                # LRU touch
                shard.cache[key] = shard.cache.pop(key)
                return shard.cache[key]

            # In-flight dedup
            if key in shard.in_flight:
                return_future = shard.in_flight[key]
            else:
                loop = asyncio.get_running_loop()
                return_future = loop.create_future()
                shard.in_flight[key] = return_future

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            content = await self._call_api(messages, temperature)
        except Exception:
            async with shard.lock:
                shard.in_flight.pop(key, None)
            return_future.cancel()
            raise

        async with shard.lock:
            if key not in shard.cache and len(shard.cache) >= _LLM_PER_SHARD_MAXSIZE:
                shard.cache.pop(next(iter(shard.cache)))
            shard.cache[key] = content
            shard.in_flight.pop(key, None)

        return_future.set_result(content)
        return content


def get_llm_service(model: str | None = None, api_key: str | None = None) -> LLMService:
    return LLMService(model=model, api_key=api_key)


llm_service = get_llm_service()
