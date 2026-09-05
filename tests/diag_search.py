"""Quick diagnostic — trace where BM25 search hangs with 16 concurrent requests."""
import asyncio
import time
from kbquant.client import QuantClient

BASE_URL = "http://localhost:8000"
CLIENT_INSTANCES = 2


async def one_request(client, idx):
    from kbquant.schemas.search import SearchRequest
    req = SearchRequest(query_text="test", mode="bm25", limit=10)
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            client.search.search(req), timeout=15.0
        )
        elapsed = time.perf_counter() - t0
        return f"[{idx:>3}] OK {elapsed:.2f}s items={len(result.items)}"
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - t0
        return f"[{idx:>3}] TIMEOUT after {elapsed:.1f}s"
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return f"[{idx:>3}] ERROR {type(e).__name__}: {str(e)[:100]}"


async def main():
    print("=== BM25 快速诊断 (16 并发) ===")
    clients = [
        QuantClient(BASE_URL, timeout=15.0, max_connections=200, max_keepalive_connections=50)
        for _ in range(CLIENT_INSTANCES)
    ]

    tasks = [one_request(clients[i % CLIENT_INSTANCES], i) for i in range(16)]
    results = await asyncio.gather(*tasks)

    for r in results:
        print(r)

    close_tasks = [c.close() for c in clients]
    await asyncio.gather(*close_tasks)

    print("\n=== BM25 单次请求 (做对比) ===")
    client = QuantClient(BASE_URL, timeout=15.0)
    print(await one_request(client, 0))
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
