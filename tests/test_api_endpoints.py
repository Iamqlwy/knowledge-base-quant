"""全面测试 KBQuant API 各接口"""
import asyncio
import sys
import time
from kbquant.client import QuantClient
from kbquant.schemas.search import SearchRequest

BASE_URL = "http://localhost:8000"
results = []


async def test(name, coro):
    """运行测试并记录结果"""
    t0 = time.time()
    try:
        result = await coro
        elapsed = time.time() - t0
        r = repr(result)[:120]
        results.append((name, "✅ PASS", f"{elapsed:.2f}s", r))
        print(f"  ✅ {name} ({elapsed:.2f}s)")
        return result
    except Exception as e:
        elapsed = time.time() - t0
        results.append((name, "❌ FAIL", f"{elapsed:.2f}s", str(e)[:200]))
        print(f"  ❌ {name} ({elapsed:.2f}s) → {e}")
        return None


def _items(resp):
    """安全提取分页响应中的 items"""
    if resp is None:
        return []
    if hasattr(resp, "items"):
        return resp.items
    if isinstance(resp, dict):
        return resp.get("items", [])
    return []


def _id(item):
    if hasattr(item, "id"):
        return item.id
    if isinstance(item, dict):
        return item.get("id")
    return None


async def main():
    async with QuantClient(BASE_URL, timeout=30.0) as c:
        # ── 1. Health ──
        print("\n═══ 1. Health ═══")
        await test("health", c.health())

        # ── 2. Information ──
        print("\n═══ 2. Information ═══")
        info_list = await test("information.list", c.information.list(page_size=3))
        items = _items(info_list)
        if items:
            fid = _id(items[0])
            await test("information.get", c.information.get(fid))
            await test("information.get_many", c.information.get_many([fid]))
            await test("information.get_entities", c.information.get_entities(fid))

        # ── 3. Entities ──
        print("\n═══ 3. Entities ═══")
        ent_list = await test("entities.list", c.entities.list(page_size=3))
        eitems = _items(ent_list)
        if eitems:
            eid = _id(eitems[0])
            await test("entities.get_relationships", c.entities.get_relationships(eid))
            await test("entities.impact_path", c.entities.impact_path(eid, depth=2))

        # ── 4. Nodes ──
        print("\n═══ 4. Nodes ═══")
        node_list = await test("nodes.list", c.nodes.list(page_size=3))
        nitems = _items(node_list)
        if nitems:
            nid = _id(nitems[0])
            await test("nodes.get", c.nodes.get(nid))
            await test("nodes.get_many", c.nodes.get_many([nid]))
            await test("nodes.get_attachments", c.nodes.get_attachments(nid))
            await test("nodes.get_current_state", c.nodes.get_current_state(nid))
            await test("nodes.get_state_history", c.nodes.get_state_history(nid))
        await test("nodes.list_names_and_aliases", c.nodes.list_names_and_aliases())

        # ── 5. Analysis ──
        print("\n═══ 5. Analysis ═══")
        ana_list = await test("analysis.list", c.analysis.list(page_size=3))
        aitems = _items(ana_list)
        if aitems:
            aid = _id(aitems[0])
            await test("analysis.get", c.analysis.get(aid))
            await test("analysis.get_many", c.analysis.get_many([aid]))

        # ── 6. Trading ──
        print("\n═══ 6. Trading ═══")
        trade_list = await test("trading.list", c.trading.list(page_size=3))
        titems = _items(trade_list)
        if titems:
            tid = _id(titems[0])
            await test("trading.get", c.trading.get(tid))
            await test("trading.get_many", c.trading.get_many([tid]))

        # ── 7. Feedback ──
        print("\n═══ 7. Feedback ═══")
        await test("feedback.list", c.feedback.list(page_size=3))

        # ── 8. Search ──
        print("\n═══ 8. Search ═══")
        await test("search.hybrid", c.search.search(SearchRequest(
            query_text="美联储加息", mode="hybrid", limit=5
        )))
        await test("search.bm25", c.search.search(SearchRequest(
            query_text="通货膨胀", mode="bm25", limit=5
        )))
        await test("search.embedding", c.search.search(SearchRequest(
            query_text="GDP增长", mode="embedding", limit=5
        )))

        # ── 9. Pipeline ──
        print("\n═══ 9. Pipeline ═══")
        await test("pipeline.stats", c.pipeline.stats())
        await test("pipeline.list_queue", c.pipeline.list_queue(page_size=3))

        # ── 10. Validity ──
        print("\n═══ 10. Validity ═══")
        await test("validity.list", c.validity.list())

        # ── 11. Conflicts ──
        print("\n═══ 11. Conflicts ═══")
        await test("conflicts.list", c.conflicts.list(page_size=3))

        # ── 12. Ranking ──
        print("\n═══ 12. Ranking ═══")
        await test("ranking.list", c.ranking.list())

        # ── 13. Evidence ──
        print("\n═══ 13. Evidence ═══")
        if nitems:
            await test("evidence.trace_node", c.evidence.trace_node(_id(nitems[0])))
        if eitems:
            await test("evidence.trace(entity)", c.evidence.trace("entity", _id(eitems[0])))

        # ── 14. Macro Report ──
        print("\n═══ 14. Macro Report ═══")
        await test("macro_report.get_current", c.macro_report.get_current())
        await test("macro_report.get_history", c.macro_report.get_history())

        # ── 15. Preferences ──
        print("\n═══ 15. Preferences ═══")
        await test("preferences.get_market_cognition", c.preferences.get_market_cognition())
        await test("preferences.get_structured", c.preferences.get_structured())

        # ── Summary ──
        print("\n" + "=" * 70)
        passed = sum(1 for r in results if r[1] == "✅ PASS")
        failed = sum(1 for r in results if r[1] == "❌ FAIL")
        print(f"总计: {len(results)} 项测试, ✅ {passed} 通过, ❌ {failed} 失败")
        print("=" * 70)

        if failed > 0:
            print("\n失败项详情:")
            for name, status, elapsed, detail in results:
                if status == "❌ FAIL":
                    print(f"  ❌ {name}: {detail}")
        return failed


if __name__ == "__main__":
    failed = asyncio.run(main())
    sys.exit(1 if failed else 0)
