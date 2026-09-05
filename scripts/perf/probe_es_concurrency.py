from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from elasticsearch import AsyncElasticsearch

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
PREFIX = os.getenv("ELASTICSEARCH_INDEX_PREFIX", "quant_kb")

FINANCE_TERMS = [
    "央行", "降准", "货币政策", "美联储", "利率", "A股", "量化", "交易",
    "白酒", "茅台", "科技", "人工智能", "芯片", "黄金", "通胀", "房地产",
    "能源", "石油", "新能源", "银行", "国债", "人民币", "汇率", "医药",
    "消费", "半导体", "基建", "GDP", "贸易", "出口",
]


@dataclass
class RoundReport:
    concurrency: int
    ok: int
    fail: int
    wall_s: float
    latencies_ms: list[float] = field(default_factory=list)
    hits: list[int] = field(default_factory=list)
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

    @property
    def avg_hits(self) -> float:
        return statistics.mean(self.hits) if self.hits else 0.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = (len(xs) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _random_hanzi(length: int = 4) -> str:
    return "".join(chr(random.randint(0x4E00, 0x9FFF)) for _ in range(length))


def _random_query() -> str:
    terms = random.sample(FINANCE_TERMS, 3)
    return " ".join([*terms, _random_hanzi(4)])


def _build_body(query_text: str, size: int) -> dict:
    return {
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query_text,
                            "fields": ["title^2", "body"],
                            "fuzziness": "AUTO",
                        }
                    }
                ],
                "filter": [],
            }
        },
        "size": size,
    }


async def _one_search(es: AsyncElasticsearch, index: str, size: int) -> tuple[bool, float, int, str]:
    query_text = _random_query()
    body = _build_body(query_text, size)
    started = time.monotonic()
    try:
        resp = await es.search(index=index, body=body, request_cache=False)
        elapsed_ms = (time.monotonic() - started) * 1000
        hits = len(resp["hits"]["hits"])
        return True, elapsed_ms, hits, ""
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        return False, elapsed_ms, 0, f"{type(exc).__name__}: {str(exc)[:240]}"


async def run_round(es: AsyncElasticsearch, index: str, size: int, concurrency: int) -> RoundReport:
    started = time.monotonic()
    results = await asyncio.gather(*[_one_search(es, index, size) for _ in range(concurrency)])
    wall_s = time.monotonic() - started
    report = RoundReport(concurrency=concurrency, ok=0, fail=0, wall_s=wall_s)
    for ok, latency_ms, hits, error in results:
        if ok:
            report.ok += 1
            report.latencies_ms.append(latency_ms)
            report.hits.append(hits)
        else:
            report.fail += 1
            report.errors.append(error)
    return report


def print_round(report: RoundReport) -> None:
    status = "OK" if report.success else "FAIL"
    print(
        f"{status:4} conc={report.concurrency:<5} ok={report.ok:<5} fail={report.fail:<5} "
        f"wall={report.wall_s:7.2f}s p50={report.p50_ms:7.0f}ms "
        f"p95={report.p95_ms:7.0f}ms p99={report.p99_ms:7.0f}ms "
        f"req/s={report.rps:7.2f} avg_hits={report.avg_hits:5.1f}",
        flush=True,
    )
    if report.errors:
        for err in list(dict.fromkeys(report.errors))[:3]:
            print(f"  error: {err}", flush=True)


async def probe(args: argparse.Namespace) -> list[RoundReport]:
    connections = max(args.connections, args.max_concurrency + 50)
    es = AsyncElasticsearch(
        ES_URL,
        connections_per_node=connections,
        request_timeout=args.timeout,
        max_retries=0,
    )
    reports: list[RoundReport] = []
    low = 0
    high: int | None = None
    current = args.start
    try:
        print(f"ES={ES_URL} index={args.index} timeout={args.timeout}s connections={connections}", flush=True)
        print("ping=", await es.ping(), flush=True)
        stats = await es.count(index=args.index)
        print(f"docs={stats['count']} size={args.size}", flush=True)

        print("\n[exponential]", flush=True)
        while current <= args.max_concurrency:
            report = await run_round(es, args.index, args.size, current)
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
            report = await run_round(es, args.index, args.size, mid)
            reports.append(report)
            print_round(report)
            if report.success:
                low = mid
            else:
                high = mid
            await asyncio.sleep(args.pause)

        print(f"\nRESULT safe_max_concurrency={low} first_failure={high}", flush=True)
        return reports
    finally:
        await es.close()


def print_summary(reports: list[RoundReport]) -> None:
    successful = [r for r in reports if r.success]
    if not reports:
        return
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
    errors = [err for r in reports for err in r.errors]
    if errors:
        print(f"unique_errors={len(set(errors))}", flush=True)
        for err in list(dict.fromkeys(errors))[:5]:
            print(f"  {err}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe direct Elasticsearch search concurrency.")
    parser.add_argument("--index", default=f"{PREFIX}_raw_info")
    parser.add_argument("--start", type=int, default=20)
    parser.add_argument("--max-concurrency", type=int, default=1280)
    parser.add_argument("--size", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument("--connections", type=int, default=1500)
    return parser.parse_args()


async def main() -> None:
    reports = await probe(parse_args())
    print_summary(reports)


if __name__ == "__main__":
    asyncio.run(main())
