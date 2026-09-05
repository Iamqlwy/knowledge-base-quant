import argparse, asyncio, statistics, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kbquant.database import async_session, engine
from kbquant.services.search_service import SearchService


def _pct(values, p):
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * p))))
    return xs[idx]


async def _run(args):
    latencies = []
    errors = 0
    sem = asyncio.Semaphore(args.concurrency)
    total_started = time.perf_counter()

    async def _one():
        nonlocal errors
        async with sem:
            async with async_session() as session:
                svc = SearchService(session)
                started = time.perf_counter()
                try:
                    await svc.search(args.query, mode=args.mode, limit=args.limit)
                    latencies.append((time.perf_counter() - started) * 1000)
                except Exception as exc:
                    errors += 1
                    print(f"  error: {exc}")

    print(f"持续并发压测: query=\\\"\"{args.query}\\\"\" mode={args.mode} concurrency={args.concurrency}")
    while len(latencies) + errors < args.total:
        tasks = [_one() for _ in range(min(args.concurrency, args.total - len(latencies) - errors))]
        await asyncio.gather(*tasks)
        remaining = args.total - len(latencies) - errors
        print(f"  进度: {len(latencies)}/{args.total} ok={len(latencies)} fail={errors} 剩余={remaining}")
        if remaining > 0 and remaining < args.concurrency:
            pass

    wall_s = time.perf_counter() - total_started
    await engine.dispose()

    print()
    if latencies:
        throughput = len(latencies) / wall_s if wall_s > 0 else 0
        print(f"总吞吐: {throughput:.2f} req/s")
        print(f"延迟 (ms): avg={statistics.mean(latencies):.2f} p50={_pct(latencies, 0.50):.2f} "
              f"p95={_pct(latencies, 0.95):.2f} p99={_pct(latencies, 0.99):.2f}")
        print(f"耗时: wall={wall_s:.2f}s ok={len(latencies)} fail={errors}")
    else:
        print(f"全部失败! fail={errors}")


def main():
    parser = argparse.ArgumentParser(description="搜索链路持续并发压测")
    parser.add_argument("--query", default="降准 央行 货币政策")
    parser.add_argument("--mode", default="hybrid", choices=["hybrid", "embedding", "bm25"])
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--total", type=int, default=100, help="总请求数")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
