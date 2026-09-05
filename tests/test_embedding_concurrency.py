"""嵌入服务并发极限测试。

用法:
    python test_embedding_concurrency.py           # 自动梯度加压
    python test_embedding_concurrency.py --fixed 50  # 固定50并发
    python test_embedding_concurrency.py --batch    # 测试批量模式
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

# 自动加载项目根目录的 .env
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

# ---------------------------------------------------------------------------
# 复制自 kbquant.config / kbquant.services.embedding_service
# (不 import 是为了不打折扣地绕过 Semaphore 和 rate-limit)
# ---------------------------------------------------------------------------
API_KEY = os.getenv("EMBEDDING_API_KEY", "")
BASE_URL = os.getenv("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))

TEST_TEXTS = [
    "人工智能是计算机科学的一个分支，旨在创造能够模拟人类智能的系统。",
    "量化交易使用数学模型来分析金融市场并做出交易决策。",
    "自然语言处理是人工智能领域中研究计算机与人类语言交互的分支。",
    "深度学习是机器学习的一个子集，使用多层神经网络来学习数据的表示。",
    "风险管理是金融领域中识别、评估和控制潜在损失的过程。",
    "中国A股市场是全球第二大股票市场，拥有超过5000家上市公司。",
    "Python是一种广泛使用的高级编程语言，以其简洁和可读性著称。",
    "区块链技术是一种去中心化的分布式账本技术，具有不可篡改的特性。",
    "云计算通过互联网提供按需计算资源，包括服务器、存储和应用程序。",
    "大数据分析涉及处理和分析海量数据集以发现模式和趋势。",
]

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    ok: bool
    latency_ms: float
    text_len: int
    vector_dim: int = 0
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
    errors: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.ok / self.total if self.total > 0 else 0.0


# ---------------------------------------------------------------------------
# 核心测试逻辑
# ---------------------------------------------------------------------------


def _build_client() -> AsyncOpenAI:
    kwargs = {"base_url": BASE_URL}
    if API_KEY:
        kwargs["api_key"] = API_KEY
    return AsyncOpenAI(**kwargs)


async def _one_embedding(client: AsyncOpenAI, text: str) -> TaskResult:
    t0 = time.monotonic()
    try:
        resp = await client.embeddings.create(
            model=MODEL,
            input=[text],
            dimensions=DIMENSION,
        )
        elapsed = (time.monotonic() - t0) * 1000
        vec = resp.data[0].embedding
        return TaskResult(ok=True, latency_ms=elapsed, text_len=len(text), vector_dim=len(vec))
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return TaskResult(ok=False, latency_ms=elapsed, text_len=len(text), error=str(exc))


async def _run_round(client: AsyncOpenAI, concurrency: int, texts: list[str]) -> RoundReport:
    """以固定并发数发出一批请求，全部完成后统计结果。"""
    report = RoundReport(concurrency=concurrency, total=len(texts), ok=0, fail=0)
    sem = asyncio.Semaphore(concurrency)

    async def _worker(text: str) -> TaskResult:
        async with sem:
            return await _one_embedding(client, text)

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
        sorted_lat = sorted(report.latencies_ms)
        report.p50_ms = _percentile(sorted_lat, 0.50)
        report.p95_ms = _percentile(sorted_lat, 0.95)
        report.p99_ms = _percentile(sorted_lat, 0.99)
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


# ---------------------------------------------------------------------------
# 梯度加压
# ---------------------------------------------------------------------------


async def _ramp_test(
    client: AsyncOpenAI,
    texts: list[str],
    start: int,
    step: int,
    max_concurrency: Optional[int],
    stop_on_first_failure: bool,
) -> list[RoundReport]:
    """逐步增大并发数，直到遇到失败或达到上限。"""
    reports: list[RoundReport] = []
    concurrency = start

    while True:
        if max_concurrency and concurrency > max_concurrency:
            break

        print(f"\n{'='*60}")
        print(f"  🧪 测试并发数: {concurrency}  (请求数: {len(texts)})")
        print(f"{'='*60}")

        report = await _run_round(client, concurrency, texts)
        reports.append(report)
        _print_round(report)

        if report.fail > 0 and stop_on_first_failure:
            print("\n⚠️  出现失败，停止加压。")
            break

        # 如果成功率低于 50%，也停下来
        if report.success_rate < 0.5:
            print("\n⚠️  成功率跌破 50%，停止加压。")
            break

        # 如果 p50 延迟超过 30 秒，认为已经饱和
        if report.p50_ms > 30_000:
            print("\n⚠️  P50 延迟超过 30 秒，认为已达极限。")
            break

        concurrency += step

        # 轮间休息，避免叠加
        await asyncio.sleep(2)

    return reports


# ---------------------------------------------------------------------------
# 批量模式测试
# ---------------------------------------------------------------------------


async def _batch_test(
    client: AsyncOpenAI,
    texts: list[str],
    start_batch: int,
    max_batch: int,
    step: int,
) -> list[RoundReport]:
    """测试批量模式下不同 batch size 的吞吐变化。"""
    reports: list[RoundReport] = []

    print("\n" + "=" * 60)
    print("  📦 批量模式测试 — 固定并发=1，逐批发送")
    print("=" * 60)

    for batch_size in range(start_batch, max_batch + 1, step):
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]

        all_latencies: list[float] = []
        ok = fail = 0
        errors: list[str] = []

        t0 = time.monotonic()
        for batch in batches:
            try:
                resp = await client.embeddings.create(
                    model=MODEL,
                    input=batch,
                    dimensions=DIMENSION,
                )
                ok += len(resp.data)
                all_latencies.append((time.monotonic() - t0) * 1000 / len(batch))
            except Exception as exc:
                fail += len(batch)
                errors.append(str(exc))
        wall = time.monotonic() - t0

        total = ok + fail
        report = RoundReport(
            concurrency=batch_size,
            total=total,
            ok=ok,
            fail=fail,
            latencies_ms=all_latencies,
            throughput_rps=total / wall if wall > 0 else 0.0,
            errors=errors,
        )
        if all_latencies:
            sorted_lat = sorted(all_latencies)
            report.p50_ms = _percentile(sorted_lat, 0.50)
            report.p95_ms = _percentile(sorted_lat, 0.95)
        reports.append(report)
        _print_round(report)

    return reports


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def _print_round(report: RoundReport) -> None:
    print(f"  成功: {report.ok}  失败: {report.fail}  "
          f"成功率: {report.success_rate:.1%}  吞吐: {report.throughput_rps:.1f} req/s")
    print(f"  延迟 (ms) — P50: {report.p50_ms:.0f}  P95: {report.p95_ms:.0f}  "
          f"P99: {report.p99_ms:.0f}")
    if report.errors:
        unique = list(dict.fromkeys(report.errors))
        for e in unique[:3]:
            print(f"  ❌ {e[:120]}")


def _print_summary(reports: list[RoundReport]) -> None:
    print("\n" + "=" * 70)
    print("  📊 汇总")
    print("=" * 70)
    print(f"{'并发':>5} {'成功':>5} {'失败':>5} {'成功率':>7}  "
          f"{'P50(ms)':>8} {'P95(ms)':>8} {'P99(ms)':>8} {'吞吐(rps)':>10}")
    print("-" * 70)
    for r in reports:
        print(f"{r.concurrent:5d} {r.ok:5d} {r.fail:5d} {r.success_rate:6.1%}  "
              f"{r.p50_ms:8.0f} {r.p95_ms:8.0f} {r.p99_ms:8.0f} {r.throughput_rps:10.1f}")

    # 找到极限并发（成功率 >= 95% 的最大并发）
    safe = [r for r in reports if r.success_rate >= 0.95]
    if safe:
        best = safe[-1]
        print(f"\n  🎯 安全最大并发: {best.concurrent}  "
              f"(P50={best.p50_ms:.0f}ms, 吞吐={best.throughput_rps:.1f} req/s)")

    # 找到吞吐最高的点
    best_tp = max(reports, key=lambda r: r.throughput_rps)
    print(f"  🚀 最大吞吐: {best_tp.throughput_rps:.1f} req/s @ 并发={best_tp.concurrency}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args():
    import argparse

    p = argparse.ArgumentParser(
        description="嵌入服务并发极限测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--fixed", type=int, default=None,
                   help="固定并发数（跳过梯度加压）")
    p.add_argument("--start", type=int, default=1,
                   help="梯度起始并发数 (默认: 1)")
    p.add_argument("--step", type=int, default=5,
                   help="梯度步长 (默认: 5)")
    p.add_argument("--max", type=int, default=None,
                   help="梯度最大并发数 (默认: 不设上限)")
    p.add_argument("--requests", type=int, default=50,
                   help="每轮请求总数 (默认: 50)")
    p.add_argument("--batch", action="store_true",
                   help="批量模式：测试不同 batch size")
    p.add_argument("--batch-start", type=int, default=1,
                   help="批量起始大小 (默认: 1)")
    p.add_argument("--batch-max", type=int, default=20,
                   help="批量最大大小 (默认: 20)")
    p.add_argument("--batch-step", type=int, default=2,
                   help="批量步长 (默认: 2)")
    p.add_argument("--no-stop", action="store_true",
                   help="不因首次失败停止梯度加压")
    p.add_argument("--api-key", type=str, default=None,
                   help="API Key（也可通过环境变量设置）")
    return p.parse_args()


async def main() -> None:
    args = _parse_args()

    global API_KEY
    if args.api_key:
        API_KEY = args.api_key

    client = _build_client()

    # 准备测试文本
    base_texts = TEST_TEXTS
    if args.requests > len(base_texts):
        repeat = math.ceil(args.requests / len(base_texts))
        base_texts = (base_texts * repeat)[:args.requests]
    else:
        base_texts = base_texts[:args.requests]

    print("=" * 60)
    print("  🔬 嵌入服务并发极限测试")
    print("=" * 60)
    print(f"  API: {BASE_URL}")
    print(f"  Model: {MODEL}  Dim: {DIMENSION}")
    print(f"  每轮请求数: {len(base_texts)}")
    print(f"  测试文本数: {len(TEST_TEXTS)} (平均长度 {sum(len(t) for t in TEST_TEXTS)//len(TEST_TEXTS)} 字)")

    # --- 预热 ---
    print("\n  🔥 预热中...")
    warm = await _run_round(client, 2, base_texts[:5])
    if warm.fail > 0:
        print(f"  ❌ 预热失败: {warm.errors[0][:150]}")
        return
    print(f"  ✅ 预热完成 (P50={warm.p50_ms:.0f}ms)")

    reports: list[RoundReport] = []

    if args.batch:
        reports = await _batch_test(client, base_texts, args.batch_start, args.batch_max, args.batch_step)
    elif args.fixed is not None:
        print(f"\n  🎯 固定并发模式: {args.fixed}")
        r = await _run_round(client, args.fixed, base_texts)
        reports.append(r)
        _print_round(r)
    else:
        reports = await _ramp_test(
            client, base_texts,
            start=args.start,
            step=args.step,
            max_concurrency=args.max,
            stop_on_first_failure=not args.no_stop,
        )

    _print_summary(reports)


if __name__ == "__main__":
    asyncio.run(main())
