"""搜索链路基线脚本。

用法:
    python scripts/perf/profile_search.py --query "降准 央行 货币政策" --rounds 5
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kbquant.database import async_session, engine
from kbquant.services.search_service import SearchService


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[index]


async def _run(args: argparse.Namespace) -> None:
    if args.concurrency > 1:
        return await _run_concurrent(args)
    hybrid_latencies: list[float] = []
    granularity_latencies: list[float] = []

    print(f"开始搜索基线测量，共 {args.rounds} 轮。")
    async with async_session() as session:
        svc = SearchService(session)
        for round_idx in range(1, args.rounds + 1):
            t0 = time.perf_counter()
            hybrid = await svc.hybrid_search(
                args.query,
                filters=None,
                weights=None,
                limit=args.limit,
            )
            hybrid_elapsed = (time.perf_counter() - t0) * 1000
            hybrid_latencies.append(hybrid_elapsed)

            t1 = time.perf_counter()
            multi = await svc.multi_granularity_search(
                args.query,
                granularities=args.granularities,
                filters=None,
                limit_per=args.limit_per,
            )
            multi_elapsed = (time.perf_counter() - t1) * 1000
            granularity_latencies.append(multi_elapsed)

            print(
                f"[{round_idx}/{args.rounds}] "
                f"hybrid={hybrid_elapsed:.2f}ms items={len(hybrid.get('items', []))} "
                f"multi={multi_elapsed:.2f}ms groups={len(multi.get('granularities', {}))}"
            )

    await engine.dispose()

    print("\n汇总结果")
    print(
        "hybrid: "
        f"avg={statistics.mean(hybrid_latencies):.2f}ms "
        f"p50={_pct(hybrid_latencies, 0.50):.2f}ms "
        f"p95={_pct(hybrid_latencies, 0.95):.2f}ms"
    )
    print(
        "multi:  "
        f"avg={statistics.mean(granularity_latencies):.2f}ms "
        f"p50={_pct(granularity_latencies, 0.50):.2f}ms "
        f"p95={_pct(granularity_latencies, 0.95):.2f}ms"
    )


async def _run_concurrent(args: argparse.Namespace) -> None:
    latencies: list[float] = []
    errors = 0
    sem = asyncio.Semaphore(args.concurrency)

    async def _one() -> None:
        nonlocal errors
        async with sem:
            async with async_session() as session:
                svc = SearchService(session)
                started = time.perf_counter()
                try:
                    await svc.search(args.query, mode="hybrid", limit=args.limit)
                    latencies.append((time.perf_counter() - started) * 1000)
                except Exception as exc:
                    errors += 1
                    print(f"  error: {exc}")

    print(f"并发压测: query={args.query!r} concurrency={args.concurrency}")
    started = time.perf_counter()
    tasks = [_one() for _ in range(args.concurrency)]
    await asyncio.gather(*tasks)
    wall_s = time.perf_counter() - started

    if latencies:
        throughput = len(latencies) / wall_s if wall_s > 0 else 0
        print(f"\n吞吐: {throughput:.2f} req/s")
        print(f"延迟: avg={statistics.mean(latencies):.2f}ms "
              f"p50={_pct(latencies, 0.50):.2f}ms "
              f"p95={_pct(latencies, 0.95):.2f}ms "
              f"p99={_pct(latencies, 0.99):.2f}ms")
        print(f"耗时: wall={wall_s:.2f}s ok={len(latencies)} fail={errors}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="搜索链路性能基线脚本")
    parser.add_argument("--query", default="降准 央行 货币政策", help="查询词")
    parser.add_argument("--rounds", type=int, default=3, help="重复轮数")
    parser.add_argument("--limit", type=int, default=10, help="hybrid_search 返回条数")
    parser.add_argument("--limit-per", type=int, default=5, help="multi_granularity_search 单类返回条数")
    parser.add_argument("--concurrency", type=int, default=1, help="并发数 (>1 启用并发压测)")
    parser.add_argument(
        "--granularities",
        nargs="+",
        default=["raw_info", "analysis", "nodes"],
        help="multi_granularity_search 的粒度列表",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
