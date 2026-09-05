from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from kbquant.config import settings  # noqa: E402


@dataclass
class RoundReport:
    concurrency: int
    ok: int
    fail: int
    wall_s: float
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.fail == 0

    @property
    def p50_ms(self) -> float:
        return _percentile(self.latencies_ms, 0.50)

    @property
    def p95_ms(self) -> float:
        return _percentile(self.latencies_ms, 0.95)

    @property
    def p99_ms(self) -> float:
        return _percentile(self.latencies_ms, 0.99)

    @property
    def rps(self) -> float:
        return self.ok / self.wall_s if self.wall_s > 0 else 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = (len(xs) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _asyncpg_dsn() -> str:
    url = make_url(settings.database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def _load_vectors(limit: int) -> list[str]:
    conn = await asyncpg.connect(_asyncpg_dsn(), timeout=10)
    try:
        rows = await conn.fetch(
            """
            select embedding::text as embedding
            from raw_information tablesample system (1)
            where embedding is not null
            limit $1
            """,
            limit,
        )
        if len(rows) < limit:
            rows = await conn.fetch(
                """
                select embedding::text as embedding
                from raw_information
                where embedding is not null
                limit $1
                """,
                limit,
            )
        return [r["embedding"] for r in rows]
    finally:
        await conn.close()


async def _one_query(
    pool: asyncpg.Pool,
    mode: str,
    vectors: list[str],
    acquire_timeout: float,
) -> tuple[bool, float, str]:
    started = time.monotonic()
    try:
        async with pool.acquire(timeout=acquire_timeout) as conn:
            if mode == "simple":
                await conn.fetchval("select 1")
            elif mode == "indexed_read":
                await conn.fetchval(
                    "select id from raw_information order by id offset $1 limit 1",
                    random.randint(0, 1000),
                )
            elif mode == "vector":
                vector = random.choice(vectors)
                await conn.fetch(
                    """
                    select id
                    from raw_information
                    where embedding is not null
                    order by embedding <=> $1::vector
                    limit 5
                    """,
                    vector,
                )
            else:
                raise ValueError(f"unknown mode: {mode}")
        return True, (time.monotonic() - started) * 1000, ""
    except Exception as exc:
        return False, (time.monotonic() - started) * 1000, f"{type(exc).__name__}: {str(exc)[:240]}"


async def run_round(args: argparse.Namespace, concurrency: int, vectors: list[str]) -> RoundReport:
    pool = await asyncpg.create_pool(
        _asyncpg_dsn(),
        min_size=0,
        max_size=concurrency,
        timeout=args.connect_timeout,
        command_timeout=args.timeout,
    )
    started = time.monotonic()
    try:
        results = await asyncio.gather(
            *[_one_query(pool, args.mode, vectors, args.connect_timeout) for _ in range(concurrency)]
        )
    finally:
        await pool.close()
    wall_s = time.monotonic() - started

    report = RoundReport(concurrency=concurrency, ok=0, fail=0, wall_s=wall_s)
    for ok, latency_ms, error in results:
        if ok:
            report.ok += 1
            report.latencies_ms.append(latency_ms)
        else:
            report.fail += 1
            report.errors.append(error)
    return report


def print_round(report: RoundReport) -> None:
    status = "OK" if report.success else "FAIL"
    print(
        f"{status:4} conc={report.concurrency:<5} ok={report.ok:<5} fail={report.fail:<5} "
        f"wall={report.wall_s:7.2f}s p50={report.p50_ms:7.0f}ms "
        f"p95={report.p95_ms:7.0f}ms p99={report.p99_ms:7.0f}ms req/s={report.rps:7.2f}",
        flush=True,
    )
    if report.errors:
        for err in list(dict.fromkeys(report.errors))[:3]:
            print(f"  error: {err}", flush=True)


async def probe(args: argparse.Namespace) -> list[RoundReport]:
    vectors = await _load_vectors(args.vector_samples) if args.mode == "vector" else []
    reports: list[RoundReport] = []
    low = 0
    high: int | None = None
    current = args.start

    print(f"database={settings.database_url}", flush=True)
    print(
        f"mode={args.mode} start={args.start} max={args.max_concurrency} "
        f"timeout={args.timeout}s connect_timeout={args.connect_timeout}s",
        flush=True,
    )
    if vectors:
        print(f"vector_samples={len(vectors)}", flush=True)

    print("\n[exponential]", flush=True)
    while current <= args.max_concurrency:
        report = await run_round(args, current, vectors)
        reports.append(report)
        print_round(report)
        if report.success:
            low = current
            current *= 2
            await asyncio.sleep(args.pause)
            continue
        high = current
        break

    if high is None:
        print("\nNo failure before max_concurrency; skip binary search.", flush=True)
        return reports

    print(f"\n[binary] low={low} high={high}", flush=True)
    while high - low > 1:
        mid = (low + high) // 2
        report = await run_round(args, mid, vectors)
        reports.append(report)
        print_round(report)
        if report.success:
            low = mid
        else:
            high = mid
        await asyncio.sleep(args.pause)

    print(f"\nRESULT safe_max_concurrency={low} first_failure={high}", flush=True)
    return reports


def print_summary(reports: list[RoundReport]) -> None:
    if not reports:
        return
    successful = [r for r in reports if r.success]
    print("\n[summary]", flush=True)
    if successful:
        best_safe = max(successful, key=lambda r: r.concurrency)
        print(
            f"max_success_seen={best_safe.concurrency} "
            f"p95={best_safe.p95_ms:.0f}ms rps={best_safe.rps:.2f}",
            flush=True,
        )
    best_tp = max(successful or reports, key=lambda r: r.rps)
    print(f"best_throughput=conc {best_tp.concurrency}, {best_tp.rps:.2f} req/s", flush=True)
    latencies = [lat for r in successful for lat in r.latencies_ms]
    if latencies:
        print(f"all_success_latency_mean={statistics.mean(latencies):.0f}ms", flush=True)
    errors = [err for r in reports for err in r.errors]
    if errors:
        print(f"unique_errors={len(set(errors))}", flush=True)
        for err in list(dict.fromkeys(errors))[:5]:
            print(f"  {err}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe direct Postgres concurrency.")
    parser.add_argument("--mode", choices=["simple", "indexed_read", "vector"], default="simple")
    parser.add_argument("--start", type=int, default=20)
    parser.add_argument("--max-concurrency", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--vector-samples", type=int, default=200)
    return parser.parse_args()


async def main() -> None:
    reports = await probe(parse_args())
    print_summary(reports)


if __name__ == "__main__":
    asyncio.run(main())
