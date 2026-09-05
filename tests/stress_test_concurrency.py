"""
KBQuant 并发压力测试
从 2 个并发开始，每轮翻倍，直到出现错误。
请求组成：>50% search/fetch，其余分散到其他接口。
"""
import asyncio
import random
import sys
import time
import uuid
from kbquant.client import QuantClient
from kbquant.schemas.search import SearchRequest, FetchByIdsRequest

BASE_URL = "http://localhost:8000"

# 搜索关键词池
QUERIES = [
    "美联储加息", "通货膨胀", "GDP增长", "失业率", "货币政策",
    "财政政策", "经济衰退", "股市行情", "债券收益率", "汇率波动",
    "原油价格", "黄金价格", "大宗商品", "贸易逆差", "消费者信心",
    "房地产市场", "科技股", "银行业", "制造业PMI", "零售销售",
    "利率决议", "CPI数据", "非农就业", "量化宽松", "财政赤字",
]

# ── 请求生成器 ──

def _random_search():
    return ("search", SearchRequest(
        query_text=random.choice(QUERIES), mode="hybrid", limit=10
    ))

def _random_fetch():
    return ("fetch", None)  # 需要先从 DB 拿 ID，这里用空列表

def _random_info_list():
    return ("info_list", None)

def _random_entity_list():
    return ("entity_list", None)

def _random_node_list():
    return ("node_list", None)

def _random_analysis_list():
    return ("analysis_list", None)

def _random_trading_list():
    return ("trading_list", None)

def _random_pipeline_stats():
    return ("pipeline_stats", None)

def _random_macro_report():
    return ("macro_report", None)

def _random_preferences():
    return ("preferences", None)

def _random_conflicts():
    return ("conflicts", None)

def _random_ranking():
    return ("ranking", None)

def _random_validity():
    return ("validity", None)

def _random_feedback():
    return ("feedback", None)


def build_request_batch(n: int) -> list:
    """
    生成 n 个请求。search + fetch 占比 > 50%。
    """
    requests = []
    # 搜索类占 55%
    n_search = int(n * 0.35)
    n_fetch = int(n * 0.20)
    n_other = n - n_search - n_fetch

    for _ in range(n_search):
        requests.append(_random_search())
    for _ in range(n_fetch):
        requests.append(_random_fetch())

    # 其他接口均匀分配
    other_generators = [
        _random_info_list, _random_entity_list, _random_node_list,
        _random_analysis_list, _random_trading_list, _random_pipeline_stats,
        _random_macro_report, _random_preferences, _random_conflicts,
        _random_ranking, _random_validity, _random_feedback,
    ]
    for i in range(n_other):
        gen = other_generators[i % len(other_generators)]
        requests.append(gen())

    random.shuffle(requests)
    return requests


async def execute_request(client: QuantClient, req, fetch_ids: dict):
    """执行单个请求"""
    kind, data = req
    if kind == "search":
        return await client.search.search(data)
    elif kind == "fetch":
        return await client.search.fetch_by_ids(FetchByIdsRequest(table_ids=fetch_ids))
    elif kind == "info_list":
        return await client.information.list(page_size=5)
    elif kind == "entity_list":
        return await client.entities.list(page_size=5)
    elif kind == "node_list":
        return await client.nodes.list(page_size=5)
    elif kind == "analysis_list":
        return await client.analysis.list(page_size=5)
    elif kind == "trading_list":
        return await client.trading.list(page_size=5)
    elif kind == "pipeline_stats":
        return await client.pipeline.stats()
    elif kind == "macro_report":
        return await client.macro_report.get_current()
    elif kind == "preferences":
        return await client.preferences.get_market_cognition()
    elif kind == "conflicts":
        return await client.conflicts.list(page_size=3)
    elif kind == "ranking":
        return await client.ranking.list()
    elif kind == "validity":
        return await client.validity.list()
    elif kind == "feedback":
        return await client.feedback.list(page_size=3)


async def get_sample_ids(client: QuantClient) -> dict:
    """从各表采样 ID 用于 fetch_by_ids"""
    ids = {}
    try:
        info = await client.information.list(page_size=5)
        if info.items:
            ids["raw_information"] = [str(i.id) for i in info.items[:3]]
    except Exception:
        pass
    try:
        ent = await client.entities.list(page_size=5)
        if ent.items:
            ids["entities"] = [str(e.id) for e in ent.items[:3]]
    except Exception:
        pass
    try:
        nodes = await client.nodes.list(page_size=5)
        if nodes.items:
            ids["world_nodes"] = [str(n.id) for n in nodes.items[:3]]
    except Exception:
        pass
    return ids


async def run_round(client: QuantClient, concurrency: int, fetch_ids: dict) -> dict:
    """运行一轮并发测试"""
    requests = build_request_batch(concurrency)
    t0 = time.time()
    errors = []
    status_counts = {"success": 0, "error": 0}

    # 按请求类型统计
    type_counts = {}
    type_errors = {}

    async def _do(req):
        kind = req[0]
        try:
            await execute_request(client, req, fetch_ids)
            status_counts["success"] += 1
            type_counts[kind] = type_counts.get(kind, 0) + 1
        except Exception as e:
            status_counts["error"] += 1
            type_errors[kind] = type_errors.get(kind, 0) + 1
            errors.append((kind, str(e)[:200]))

    await asyncio.gather(*[_do(r) for r in requests])
    elapsed = time.time() - t0

    return {
        "concurrency": concurrency,
        "total": concurrency,
        "success": status_counts["success"],
        "errors": status_counts["error"],
        "elapsed": elapsed,
        "rps": concurrency / elapsed if elapsed > 0 else 0,
        "type_counts": type_counts,
        "type_errors": type_errors,
        "error_details": errors[:5],  # 最多显示 5 个
    }


async def main():
    print("=" * 70)
    print("KBQuant 并发压力测试")
    print(f"目标: {BASE_URL}")
    print("搜索模式: bm25 | search+fetch 占比: ~55%")
    print("=" * 70)

    async with QuantClient(BASE_URL, timeout=60.0, max_connections=2000, max_keepalive_connections=1000) as client:
        # 预热
        print("\n预热中...")
        await client.health()
        fetch_ids = await get_sample_ids(client)
        print(f"采样 ID: {', '.join(f'{k}={len(v)}个' for k, v in fetch_ids.items())}")

        print(f"\n{'并发数':>8} {'成功':>6} {'失败':>6} {'耗时':>8} {'RPS':>8}  状态")
        print("-" * 70)

        concurrency = 2
        max_concurrency = 4096  # 安全上限

        while concurrency <= max_concurrency:
            result = await run_round(client, concurrency, fetch_ids)

            status = "✅" if result["errors"] == 0 else "❌"
            type_info = " | ".join(f"{k}:{v}" for k, v in sorted(result["type_counts"].items()))

            print(f"{result['concurrency']:>8} {result['success']:>6} {result['errors']:>6} "
                  f"{result['elapsed']:>7.2f}s {result['rps']:>7.1f}  {status}")

            if result["type_errors"]:
                err_info = " | ".join(f"{k}:{v}" for k, v in sorted(result["type_errors"].items()))
                print(f"         失败类型: {err_info}")

            if result["error_details"]:
                for kind, detail in result["error_details"][:3]:
                    print(f"         [{kind}] {detail}")

            # 出现错误则停止
            if result["errors"] > 0:
                print(f"\n{'=' * 70}")
                print(f"⚠️  在并发数 {concurrency} 时出现错误，测试终止。")
                print(f"    最大无错并发数: {concurrency // 2}")
                print(f"{'=' * 70}")
                return

            concurrency *= 2

        print(f"\n已达到最大测试并发 {max_concurrency}，全部通过。")


if __name__ == "__main__":
    asyncio.run(main())
