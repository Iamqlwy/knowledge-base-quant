from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


API_KEY = os.getenv("EMBEDDING_API_KEY", "")
BASE_URL = os.getenv("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))


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
    def rps(self) -> float:
        return self.ok / self.wall_s if self.wall_s > 0 else 0.0

    @property
    def texts_per_s(self) -> float:
        return self.ok * 10 / self.wall_s if self.wall_s > 0 else 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = (len(xs) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _random_hanzi_text(length: int = 10) -> str:
    return "".join(chr(random.randint(0x4E00, 0x9FFF)) for _ in range(length))


def _build_payload(batch_size: int, text_len: int) -> list[str]:
    return [_random_hanzi_text(text_len) for _ in range(batch_size)]


def _build_client(max_connections: int, timeout_s: float) -> AsyncOpenAI:
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max(100, min(max_connections, 1000)),
        ),
        timeout=httpx.Timeout(timeout_s),
        trust_env=False,
    )
    return AsyncOpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout=timeout_s,
        max_retries=0,
        http_client=http_client,
    )


async def _one_request(client: AsyncOpenAI, batch_size: int, text_len: int) -> tuple[bool, float, str]:
    payload = _build_payload(batch_size, text_len)
    started = time.monotonic()
    try:
        resp = await client.embeddings.create(
            model=MODEL,
            input=payload,
            dimensions=DIMENSION,
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        if len(resp.data) != batch_size:
            return False, elapsed_ms, f"bad item count: {len(resp.data)} != {batch_size}"
        bad_dims = [len(item.embedding) for item in resp.data if len(item.embedding) != DIMENSION]
        if bad_dims:
            return False, elapsed_ms, f"bad dimension: {bad_dims[0]} != {DIMENSION}"
        return True, elapsed_ms, ""
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        return False, elapsed_ms, f"{type(exc).__name__}: {str(exc)[:240]}"


async def run_round(
    client: AsyncOpenAI,
    concurrency: int,
    batch_size: int,
    text_len: int,
) -> RoundReport:
    started = time.monotonic()
    results = await asyncio.gather(
        *[_one_request(client, batch_size, text_len) for _ in range(concurrency)]
    )
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
        f"p95={report.p95_ms:7.0f}ms req/s={report.rps:7.2f} text/s={report.texts_per_s:7.2f}",
        flush=True,
    )
    if report.errors:
        for err in list(dict.fromkeys(report.errors))[:3]:
            print(f"  error: {err}", flush=True)


async def probe(args: argparse.Namespace) -> list[RoundReport]:
    max_connections = max(args.max_connections, args.max_concurrency + 50)
    client = _build_client(max_connections=max_connections, timeout_s=args.timeout)
    reports: list[RoundReport] = []
    low = 0
    high: int | None = None
    current = args.start

    try:
        print(f"API={BASE_URL} model={MODEL} dim={DIMENSION}", flush=True)
        print(
            f"batch_size={args.batch_size} text_len={args.text_len} "
            f"start={args.start} max={args.max_concurrency} timeout={args.timeout}s",
            flush=True,
        )

        print("\n[exponential]", flush=True)
        while current <= args.max_concurrency:
            report = await run_round(client, current, args.batch_size, args.text_len)
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
            report = await run_round(client, mid, args.batch_size, args.text_len)
            reports.append(report)
            print_round(report)
            if report.success:
                low = mid
            else:
                high = mid
            await asyncio.sleep(args.pause)

        if low > 0 and args.confirm:
            print(f"\n[confirm] safe={low}", flush=True)
            report = await run_round(client, low, args.batch_size, args.text_len)
            reports.append(report)
            print_round(report)
            if not report.success:
                print(
                    "confirm failed; safe_max_concurrency from binary search is not stable under the current rate window",
                    flush=True,
                )

        print(f"\nRESULT safe_max_concurrency={low} first_failure={high}", flush=True)
        return reports
    finally:
        await client.close()


async def run_fixed(args: argparse.Namespace) -> list[RoundReport]:
    max_connections = max(args.max_connections, max(args.fixed) + 50)
    client = _build_client(max_connections=max_connections, timeout_s=args.timeout)
    reports: list[RoundReport] = []
    try:
        print(f"API={BASE_URL} model={MODEL} dim={DIMENSION}", flush=True)
        print(
            f"batch_size={args.batch_size} text_len={args.text_len} "
            f"fixed={args.fixed} timeout={args.timeout}s pause={args.pause}s",
            flush=True,
        )
        for idx, concurrency in enumerate(args.fixed):
            if idx > 0 and args.pause > 0:
                print(f"\n[pause] {args.pause}s", flush=True)
                await asyncio.sleep(args.pause)
            report = await run_round(client, concurrency, args.batch_size, args.text_len)
            reports.append(report)
            print_round(report)
        return reports
    finally:
        await client.close()


def print_summary(reports: list[RoundReport]) -> None:
    if not reports:
        return
    successful = [r for r in reports if r.success]
    best_tp = max(successful or reports, key=lambda r: r.texts_per_s)
    errors = [err for r in reports for err in r.errors]
    print("\n[summary]", flush=True)
    if successful:
        print(f"max_success_seen={max(r.concurrency for r in successful)}", flush=True)
    print(
        f"best_throughput=conc {best_tp.concurrency}, "
        f"{best_tp.rps:.2f} req/s, {best_tp.texts_per_s:.2f} texts/s",
        flush=True,
    )
    if errors:
        print(f"unique_errors={len(set(errors))}", flush=True)
        for err in list(dict.fromkeys(errors))[:5]:
            print(f"  {err}", flush=True)
    latencies = [lat for r in successful for lat in r.latencies_ms]
    if latencies:
        print(f"all_success_latency_mean={statistics.mean(latencies):.0f}ms", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe embedding API concurrency with exponential + binary search.")
    parser.add_argument("--start", type=int, default=20)
    parser.add_argument("--max-concurrency", type=int, default=1280)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--text-len", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--max-connections", type=int, default=1500)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--fixed", type=int, nargs="*", default=None)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.fixed:
        reports = await run_fixed(args)
    else:
        reports = await probe(args)
    print_summary(reports)


if __name__ == "__main__":
    asyncio.run(main())
