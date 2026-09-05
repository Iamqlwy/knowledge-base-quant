"""Pipeline 单批处理基线脚本。

用法:
    python scripts/perf/profile_pipeline.py --limit 5 --concurrency 2
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kbquant.database import async_session, engine
from kbquant.models.raw_information import RawInformation
from kbquant.pipeline.worker import PipelineWorker


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[index]


async def _load_sample_ids(limit: int) -> list:
    async with async_session() as session:
        result = await session.execute(
            select(RawInformation.id)
            .order_by(RawInformation.published_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def _run(args: argparse.Namespace) -> None:
    info_ids = await _load_sample_ids(args.limit)
    if not info_ids:
        print("未找到可用于压测的 raw_information 数据，先准备样本后再运行。")
        await engine.dispose()
        return

    latencies: list[float] = []
    errors: list[str] = []
    sem = asyncio.Semaphore(max(1, args.concurrency))
    worker = PipelineWorker(async_session, batch_size=args.limit)
    total = len(info_ids)

    print(f"开始 Pipeline 基线测量，共 {total} 条，最大并发 {max(1, args.concurrency)}。")

    async def _one(index: int, raw_info_id) -> None:
        async with sem:
            started = time.perf_counter()
            try:
                await worker._process_one(SimpleNamespace(raw_info_id=raw_info_id))
                latency_ms = (time.perf_counter() - started) * 1000
                latencies.append(latency_ms)
                print(f"[{index}/{total}] raw_info_id={raw_info_id} ok {latency_ms:.2f}ms")
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000
                errors.append(str(exc))
                print(f"[{index}/{total}] raw_info_id={raw_info_id} fail {latency_ms:.2f}ms: {exc}")

    await asyncio.gather(*[
        _one(index, raw_info_id)
        for index, raw_info_id in enumerate(info_ids, start=1)
    ])
    await engine.dispose()

    print("\n汇总结果")
    if latencies:
        print(
            f"success={len(latencies)} "
            f"avg={statistics.mean(latencies):.2f}ms "
            f"p50={_pct(latencies, 0.50):.2f}ms "
            f"p95={_pct(latencies, 0.95):.2f}ms"
        )
    if errors:
        print(f"failed={len(errors)} first_error={errors[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline 单批处理性能基线脚本")
    parser.add_argument("--limit", type=int, default=3, help="最多处理多少条资讯")
    parser.add_argument("--concurrency", type=int, default=1, help="并发处理条数")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
