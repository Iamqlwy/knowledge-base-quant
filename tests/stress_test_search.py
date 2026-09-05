"""KBQuant 搜索 (BM25) 压力测试 — 逐步递增并发，找到服务极限。"""

import asyncio
import time
from kbquant.client import QuantClient, QuantClientConnectionError
from kbquant.schemas.search import SearchRequest

BASE_URL = "http://localhost:8000"
START_CONCURRENCY = 8
MAX_CONCURRENCY = 4096
CLIENT_INSTANCES = 8

# 多选几个查询词，避免缓存效应（虽然 BM25 不走 embedding 缓存）
QUERIES = [
    "美联储加息",
    "通胀预期",
    "GDP增速放缓",
    "原油价格波动",
    "房地产政策",
    "科技股财报",
    "人民币汇率",
    "供应链危机",
    "消费者信心指数",
    "全球贸易摩擦",
]


async def one_request(client: QuantClient, i: int) -> dict:
    """单次 BM25 搜索请求。"""
    query = QUERIES[i % len(QUERIES)]
    req = SearchRequest(query_text=query, mode="bm25", limit=10)
    t0 = time.perf_counter()
    try:
        result = await client.search.search(req)
        elapsed = time.perf_counter() - t0
        return {"ok": True, "elapsed": elapsed, "count": len(result.items)}
    except QuantClientConnectionError as e:
        elapsed = time.perf_counter() - t0
        return {"ok": False, "elapsed": elapsed, "error": str(e)[:120]}
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {"ok": False, "elapsed": elapsed, "error": f"{type(e).__name__}: {str(e)[:120]}"}


async def run_phase(concurrency: int, num_clients: int) -> dict:
    clients = [
        QuantClient(BASE_URL, timeout=30.0, max_connections=500, max_keepalive_connections=100)
        for _ in range(num_clients)
    ]

    t0 = time.perf_counter()
    tasks = [one_request(clients[i % num_clients], i) for i in range(concurrency)]
    results = await asyncio.gather(*tasks)
    total_time = time.perf_counter() - t0

    close_tasks = [c.close() for c in clients]
    await asyncio.gather(*close_tasks)

    ok_results = [r for r in results if r["ok"]]
    fail_results = [r for r in results if not r["ok"]]

    latencies = sorted([r["elapsed"] for r in ok_results])
    total_ok = len(ok_results)
    total_fail = len(fail_results)

    def pct(lst, p):
        return lst[min(int(len(lst) * p), len(lst) - 1)] if lst else 0

    p50 = pct(latencies, 0.5)
    p95 = pct(latencies, 0.95)
    p99 = pct(latencies, 0.99)
    max_lat = latencies[-1] if ok_results else 0
    min_lat = latencies[0] if ok_results else 0

    error_counts = {}
    for r in fail_results:
        key = r.get("error", "unknown")[:60]
        error_counts[key] = error_counts.get(key, 0) + 1

    print(
        f" 并发 {concurrency:>6} | "
        f"OK {total_ok:>6} | "
        f"FAIL {total_fail:>6} | "
        f"总耗时 {total_time:.1f}s | "
        f"QPS {concurrency / total_time:.1f} | "
        f"延迟 p50={p50:.3f}s p95={p95:.3f}s p99={p99:.3f}s min={min_lat:.3f}s max={max_lat:.3f}s"
    )

    if error_counts:
        print(f"          Errors: {error_counts}")

    return {
        "concurrency": concurrency,
        "ok": total_ok,
        "fail": total_fail,
        "total_time": total_time,
        "qps": concurrency / total_time,
        "p50": p50,
        "p95": p95,
        "p99": p99,
        "max_lat": max_lat,
        "min_lat": min_lat,
        "errors": error_counts,
    }


async def main():
    print("=" * 90)
    print(f"KBQuant BM25 搜索压力测试 — 目标: {BASE_URL}")
    print(f"客户端实例数: {CLIENT_INSTANCES}，每个 client max_connections=500")
    print(f"搜索模式: bm25, 每次返回 10 条")
    print(f"并发起点: {START_CONCURRENCY}，每轮翻倍，直到 {MAX_CONCURRENCY}")
    print("=" * 90)

    concurrency = START_CONCURRENCY
    history = []

    while concurrency <= MAX_CONCURRENCY:
        phase_result = await run_phase(concurrency, CLIENT_INSTANCES)
        history.append(phase_result)

        if phase_result["fail"] > phase_result["ok"] * 0.5:
            print(f"\n失败率超过 50%，停止递增。")
            break

        if phase_result["fail"] > 0 and phase_result["ok"] == 0:
            print(f"\n全部失败，停止测试。")
            break

        if phase_result["p99"] > 25.0:
            print(f"\np99 延迟 {phase_result['p99']:.1f}s > 25s，停止递增。")
            break

        concurrency *= 2
        await asyncio.sleep(2)

    print("\n" + "=" * 90)
    print("汇总:")
    print(f"{'并发':>8} {'成功':>6} {'失败':>6} {'QPS':>8} {'p50':>6} {'p95':>6} {'p99':>6} {'max':>6}")
    for h in history:
        print(
            f"{h['concurrency']:>8} {h['ok']:>6} {h['fail']:>6} "
            f"{h['qps']:>8.1f} {h['p50']:>5.2f}s {h['p95']:>5.2f}s "
            f"{h['p99']:>5.2f}s {h['max_lat']:>5.2f}s"
        )


if __name__ == "__main__":
    asyncio.run(main())
