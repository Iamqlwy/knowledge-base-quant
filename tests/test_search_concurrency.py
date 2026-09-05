
import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

BASE_URL = "http://localhost:8000"
SEARCH_ENDPOINT = "/api/v1/search"
REQUEST_TIMEOUT = 120.0

QUERY_TEXTS = [
    "央行 降准 货币政策",
    "美联储 加息 利率 决议",
    "A股 市场 量化 交易",
    "白酒 板块 茅台 五粮液",
    "科技 公司 人工智能 芯片",
    "黄金 价格 通胀 避险",
    "房地产 调控 信贷 政策",
    "能源 石油 天然气 价格",
    "新能源 电动车 光伏 风电",
    "银行 存款 准备金 利率",
    "国债 收益率 曲线 倒挂",
    "人民币 汇率 美元 指数",
    "云计算 大数据 数据中心",
    "医药 生物 疫苗 创新药",
    "消费 零售 电商 直播",
    "半导体 产业链 制造 封测",
    "基建 投资 地方政府 专项债",
    "通胀 就业 非农 数据",
    "GDP 增长 季度 经济",
    "贸易 逆差 顺差 进出口",
]


@dataclass
class TaskResult:
    ok: bool
    latency_ms: float
    status_code: int = 0
    result_count: int = 0
    error: str = ""


@dataclass
class RoundReport:
    concurrency: int
    total: int
    ok: int
    fail: int
    latencies_ms: list[float] = field(default_factory=list)
    throughput_rps: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.ok / self.total if self.total > 0 else 0.0


async def _one_search(client: httpx.AsyncClient, query_text: str) -> TaskResult:
    t0 = time.monotonic()
    try:
        resp = await client.post(
            SEARCH_ENDPOINT,
            json={"query_text": query_text, "mode": "bm25", "limit": 5},
            timeout=REQUEST_TIMEOUT,
        )
        elapsed = (time.monotonic() - t0) * 1000
        if resp.status_code == 200:
            data = resp.json()
            return TaskResult(
                ok=True, latency_ms=elapsed, status_code=200,
                result_count=len(data.get("items", []))
            )
        return TaskResult(
            ok=False, latency_ms=elapsed, status_code=resp.status_code,
            error=f"HTTP {resp.status_code}: {resp.text[:120]}"
        )
    except httpx.TimeoutException:
        return TaskResult(ok=False, latency_ms=(time.monotonic() - t0) * 1000, error="timeout")
    except Exception as exc:
        return TaskResult(ok=False, latency_ms=(time.monotonic() - t0) * 1000, error=str(exc))


async def _run_round(concurrency: int, texts: list[str]) -> RoundReport:
    """Run one round with fixed concurrency, return stats."""
    report = RoundReport(concurrency=concurrency, total=len(texts), ok=0, fail=0)
    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(
        max_keepalive_connections=concurrency + 10,
        max_connections=concurrency + 10,
    )

    async def _worker(text: str) -> TaskResult:
        async with sem:
            return await _one_search(client, text)

    async with httpx.AsyncClient(base_url=BASE_URL, limits=limits, timeout=REQUEST_TIMEOUT) as client:
        t0 = time.monotonic()
        results = await asyncio.gather(*[_worker(t) for t in texts])
        wall = time.monotonic() - t0

    for r in results:
        if r.ok:
            report.ok += 1
            report.latencies_ms.append(r.latency_ms)
        else:
            report.fail += 1
            report.errors.append(r.error)

    if report.latencies_ms:
        sl = sorted(report.latencies_ms)
        report.p50_ms = _percentile(sl, 0.50)
        report.p95_ms = _percentile(sl, 0.95)
        report.p99_ms = _percentile(sl, 0.99)
        report.min_ms = sl[0]
        report.max_ms = sl[-1]
    report.throughput_rps = report.total / wall if wall > 0 else 0.0
    return report


def _percentile(sorted_data: list[float], p: float) -> float:
    if not sorted_data:
        return 0.0
    idx = (len(sorted_data) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


async def _ramp_test(
    texts: list[str], start: int, step: int,
    max_concurrency: Optional[int], stop_on_first_failure: bool,
) -> list[RoundReport]:
    """Gradually increase concurrency until failure or limit."""
    reports: list[RoundReport] = []
    concurrency = start

    while True:
        if max_concurrency and concurrency > max_concurrency:
            break

        print(f"\n{'=' * 60}")
        print(f"  Concurrency: {concurrency}  (requests: {len(texts)})")
        print(f"{'=' * 60}")

        report = await _run_round(concurrency, texts)
        reports.append(report)
        _print_round(report)

        if report.fail > 0 and stop_on_first_failure:
            print("\n  Failure detected, stopping ramp.")
            break

        if report.success_rate < 0.5:
            print("\n  Success rate < 50%, stopping ramp.")
            break

        if report.p50_ms > 30_000:
            print("\n  P50 latency > 30s, stopping ramp.")
            break

        concurrency += step
        await asyncio.sleep(2)

    return reports


def _print_round(report: RoundReport) -> None:
    print(f"  OK: {report.ok}  FAIL: {report.fail}  "
          f"Rate: {report.success_rate:.1%}  RPS: {report.throughput_rps:.1f}")
    print(f"  Latency(ms) - Min: {report.min_ms:.0f}  P50: {report.p50_ms:.0f}  "
          f"P95: {report.p95_ms:.0f}  P99: {report.p99_ms:.0f}  Max: {report.max_ms:.0f}")
    if report.errors:
        for e in list(dict.fromkeys(report.errors))[:3]:
            print(f"  ERROR: {e[:150]}")


def _print_summary(reports: list[RoundReport]) -> None:
    print(f"\n{'=' * 80}")
    print("  SUMMARY")
    print(f"{'=' * 80}")
    hdr = (f"{'Conc':>5} {'OK':>5} {'FAIL':>5} {'Rate':>7}  "
           f"{'Min_ms':>8} {'P50_ms':>8} {'P95_ms':>8} {'P99_ms':>8} {'Max_ms':>8} {'RPS':>10}")
    print(hdr)
    print("-" * 80)
    for r in reports:
        row = (f"{r.concurrency:5d} {r.ok:5d} {r.fail:5d} {r.success_rate:6.1%}  "
               f"{r.min_ms:8.0f} {r.p50_ms:8.0f} {r.p95_ms:8.0f} {r.p99_ms:8.0f} {r.max_ms:8.0f} {r.throughput_rps:10.1f}")
        print(row)

    safe = [r for r in reports if r.success_rate >= 0.95]
    if safe:
        best = safe[-1]
        print(f"\n  MAX SAFE (>=95%): {best.concurrency}  "
              f"P50={best.p50_ms:.0f}ms  P95={best.p95_ms:.0f}ms  "
              f"RPS={best.throughput_rps:.1f}")
    elif reports:
        best = reports[-1]
        print(f"\n  All tested >=95%, max: {best.concurrency}  "
              f"P50={best.p50_ms:.0f}ms  RPS={best.throughput_rps:.1f}")

    if reports:
        best_tp = max(reports, key=lambda r: r.throughput_rps)
        print(f"  PEAK RPS: {best_tp.throughput_rps:.1f} @ conc={best_tp.concurrency}")


def _parse_args():
    import argparse

    p = argparse.ArgumentParser(description="BM25 Search Concurrency Test")
    p.add_argument("--fixed", type=int, default=None, help="Fixed concurrency")
    p.add_argument("--start", type=int, default=5, help="Start concurrency (default: 5)")
    p.add_argument("--step", type=int, default=5, help="Ramp step (default: 5)")
    p.add_argument("--max", type=int, default=None, help="Max concurrency")
    p.add_argument("--requests", type=int, default=50, help="Requests per round (default: 50)")
    p.add_argument("--no-stop", action="store_true", help="Don't stop on first failure")
    p.add_argument("--base-url", type=str, default=BASE_URL, help=f"Service URL (default: {BASE_URL})")
    p.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT, help="Per-request timeout seconds")
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    global BASE_URL, REQUEST_TIMEOUT
    BASE_URL = args.base_url
    REQUEST_TIMEOUT = args.timeout

    base_texts = QUERY_TEXTS
    if args.requests > len(base_texts):
        repeat = math.ceil(args.requests / len(base_texts))
        base_texts = (base_texts * repeat)[:args.requests]
    else:
        base_texts = base_texts[:args.requests]

    print("=" * 60)
    print("  BM25 Search Concurrency Test")
    print("=" * 60)
    print(f"  URL: {BASE_URL}{SEARCH_ENDPOINT}")
    print(f"  Mode: bm25")
    print(f"  Requests/round: {len(base_texts)}")
    print(f"  Timeout: {REQUEST_TIMEOUT:.0f}s")
    print(f"  Query templates: {len(QUERY_TEXTS)}")

    # Health check
    print("\n  Health check...")
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as c:
            resp = await c.get("/health")
            if resp.status_code == 200:
                print(f"  OK: {resp.json()}")
            else:
                print(f"  FAIL: HTTP {resp.status_code}")
                return
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return

    # Warmup
    print("\n  Warmup...")
    warm = await _run_round(2, base_texts[:5])
    if warm.fail > 0:
        print(f"  FAIL: {warm.errors[0][:150]}")
        return
    print(f"  OK (P50={warm.p50_ms:.0f}ms)")

    reports: list[RoundReport] = []

    if args.fixed is not None:
        print(f"\n  Fixed concurrency: {args.fixed}")
        r = await _run_round(args.fixed, base_texts)
        reports.append(r)
        _print_round(r)
    else:
        reports = await _ramp_test(
            base_texts, start=args.start, step=args.step,
            max_concurrency=args.max,
            stop_on_first_failure=not args.no_stop,
        )

    _print_summary(reports)


if __name__ == "__main__":
    asyncio.run(main())
