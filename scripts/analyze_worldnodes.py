"""分析 world_nodes 中的重复节点和缺失父子关系。"""

import sys
import asyncio
from collections import defaultdict
from difflib import SequenceMatcher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from kbquant.config import settings

async def main():
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine)

    async with session_factory() as session:
        # Fetch all active world_nodes with their parent info
        result = await session.execute(
            text("""
                SELECT id, name, node_type, description, parent_node_id, ticker, aliases
                FROM world_nodes
                WHERE is_active = true
                ORDER BY node_type, name
            """)
        )
        rows = result.fetchall()

    nodes = []
    for row in rows:
        nodes.append({
            "id": str(row[0]),
            "name": row[1],
            "node_type": row[2],
            "description": row[3] or "",
            "parent_node_id": str(row[4]) if row[4] else None,
            "ticker": row[5],
            "aliases": row[6] or [],
        })

    print(f"总节点数: {len(nodes)}")
    print()

    # =====================================================================
    # 1. 重复/近似节点检测
    # =====================================================================
    print("=" * 80)
    print("第一部分: 重复/近似节点检测")
    print("=" * 80)

    # 1a. 精确同名
    name_groups = defaultdict(list)
    for n in nodes:
        name_groups[n["name"].strip().lower()].append(n)

    dup_by_exact_name = {k: v for k, v in name_groups.items() if len(v) > 1}
    if dup_by_exact_name:
        print("\n--- 1a. 精确同名节点 ---")
        for name, group in dup_by_exact_name.items():
            print(f"  名称: '{name}' ({len(group)} 个)")
            for n in group:
                print(f"    id={n['id']} type={n['node_type']} parent={n['parent_node_id']}")
    else:
        print("\n1a. 无精确同名节点")

    # 1b. 名称包含关系 (A 完全包含 B 的名称)
    print("\n--- 1b. 名称包含关系 (潜在同一事物) ---")
    contained_pairs = []
    for i, a in enumerate(nodes):
        a_name = a["name"].strip().lower()
        if len(a_name) < 3:
            continue
        for j, b in enumerate(nodes):
            if i >= j:
                continue
            b_name = b["name"].strip().lower()
            if len(b_name) < 3:
                continue
            if a_name != b_name and (a_name in b_name or b_name in a_name):
                # One name contains the other completely
                contained_pairs.append((a, b))
    if contained_pairs:
        for a, b in contained_pairs[:30]:
            print(f"  [{a['node_type']}] '{a['name']}' ↔ [{b['node_type']}] '{b['name']}'")
            if a["parent_node_id"] and b["parent_node_id"]:
                pass  # both have parents
        if len(contained_pairs) > 30:
            print(f"  ... 共 {len(contained_pairs)} 对")
    else:
        print("  无")

    # 1c. 高相似度名称 (>0.85)
    print("\n--- 1c. 高相似度名称 (相似度>=0.85) ---")
    high_sim_pairs = []
    for i, a in enumerate(nodes):
        a_name = a["name"].strip().lower()
        if len(a_name) < 4:
            continue
        for j, b in enumerate(nodes):
            if i >= j:
                continue
            b_name = b["name"].strip().lower()
            if len(b_name) < 4:
                continue
            if a["node_type"] != b["node_type"]:
                continue  # 同类型才比较
            sim = SequenceMatcher(None, a_name, b_name).ratio()
            if sim >= 0.85 and a_name != b_name:
                high_sim_pairs.append((a, b, sim))
    high_sim_pairs.sort(key=lambda x: -x[2])
    if high_sim_pairs:
        for a, b, sim in high_sim_pairs[:30]:
            print(f"  sim={sim:.2f} [{a['node_type']}] '{a['name']}' ↔ '{b['name']}'")
            print(f"    A: id={a['id']} desc={a['description'][:60]}")
            print(f"    B: id={b['id']} desc={b['description'][:60]}")
        if len(high_sim_pairs) > 30:
            print(f"  ... 共 {len(high_sim_pairs)} 对")
    else:
        print("  无")

    # 1d. 同 ticker 多节点
    print("\n--- 1d. 同 ticker 多节点 ---")
    ticker_groups = defaultdict(list)
    for n in nodes:
        if n["ticker"]:
            ticker_groups[n["ticker"]].append(n)
    dup_tickers = {k: v for k, v in ticker_groups.items() if len(v) > 1}
    if dup_tickers:
        for ticker, group in sorted(dup_tickers.items()):
            print(f"  ticker={ticker}: {len(group)} 个节点")
            for n in group:
                print(f"    '{n['name']}' id={n['id']} type={n['node_type']}")
    else:
        print("  无")

    # 1e. 同名不同type
    print("\n--- 1e. 同名或高度相似但不同 type ---")
    cross_type_pairs = []
    for i, a in enumerate(nodes):
        a_name = a["name"].strip().lower()
        if len(a_name) < 3:
            continue
        for j, b in enumerate(nodes):
            if i >= j:
                continue
            b_name = b["name"].strip().lower()
            if len(b_name) < 3:
                continue
            if a["node_type"] == b["node_type"]:
                continue
            sim = SequenceMatcher(None, a_name, b_name).ratio()
            if sim >= 0.85:
                cross_type_pairs.append((a, b, sim))
    cross_type_pairs.sort(key=lambda x: -x[2])
    if cross_type_pairs:
        for a, b, sim in cross_type_pairs[:30]:
            print(f"  sim={sim:.2f} [{a['node_type']}] '{a['name']}' ↔ [{b['node_type']}] '{b['name']}'")
            print(f"    A: id={a['id']} parent={a['parent_node_id']}")
            print(f"    B: id={b['id']} parent={b['parent_node_id']}")
        if len(cross_type_pairs) > 30:
            print(f"  ... 共 {len(cross_type_pairs)} 对")
    else:
        print("  无")

    # =====================================================================
    # 2. 缺失父子关系检测
    # =====================================================================
    print()
    print("=" * 80)
    print("第二部分: 可能缺失的父子关系")
    print("=" * 80)

    # Build id->node lookup
    node_by_id = {n["id"]: n for n in nodes}

    # 2a. 有 parent_node_id 但 parent 不在活跃节点中的
    print("\n--- 2a. 孤儿节点 (parent_node_id 指向不存在的节点) ---")
    orphan_count = 0
    for n in nodes:
        if n["parent_node_id"] and n["parent_node_id"] not in node_by_id:
            print(f"  '{n['name']}' ({n['node_type']}) parent={n['parent_node_id']} → 不存在")
            orphan_count += 1
    if orphan_count == 0:
        print("  无")

    # 2b. company 节点没有 sector 父节点
    print("\n--- 2b. company 节点缺少 sector 父节点 ---")
    companies_without_parent = [
        n for n in nodes
        if n["node_type"] == "company" and n["parent_node_id"] is None
    ]
    if companies_without_parent:
        print(f"  共 {len(companies_without_parent)} 个公司节点没有父节点 (可能合理):")
        for n in companies_without_parent[:20]:
            desc_preview = n["description"][:80].replace("\n", " ")
            print(f"    '{n['name']}' {desc_preview}")
        if len(companies_without_parent) > 20:
            print(f"    ... 还有 {len(companies_without_parent) - 20} 个")
    else:
        print("  无")

    # 2c. 从描述中提取隐含的行业/板块关系
    print("\n--- 2c. 公司描述中提到板块但未设置 parent ---")
    sector_nodes = {n["name"].strip().lower(): n for n in nodes if n["node_type"] == "sector"}
    concept_nodes = {n["name"].strip().lower(): n for n in nodes if n["node_type"] == "concept"}
    all_parent_candidates = {**sector_nodes, **concept_nodes}

    # Common sector keywords in descriptions
    sector_keywords = [
        "板块", "行业", "赛道", "领域", "概念",
    ]

    potential_missing_links = []
    for n in nodes:
        if n["node_type"] != "company":
            continue
        if n["parent_node_id"] is not None:
            continue  # already has parent
        desc = n["description"].lower()

        # Check if any existing sector/concept name appears in description
        for cand_name, cand_node in all_parent_candidates.items():
            if cand_name in desc and cand_name != n["name"].strip().lower():
                potential_missing_links.append((n, cand_node, "desc_contains_name", 1.0))

    if potential_missing_links:
        # Dedup
        seen = set()
        unique_links = []
        for company, parent, reason, score in potential_missing_links:
            key = (company["id"], parent["id"])
            if key not in seen:
                seen.add(key)
                unique_links.append((company, parent, reason, score))
        # Sort by company name
        unique_links.sort(key=lambda x: x[0]["name"])
        for company, parent, reason, score in unique_links[:40]:
            print(f"  '{company['name']}' → parent候选: '{parent['name']}' ({parent['node_type']})")
        if len(unique_links) > 40:
            print(f"  ... 共 {len(unique_links)} 条建议")
    else:
        print("  无")

    # 2d. sector/concept 之间可能的层级关系
    print("\n--- 2d. Sector/Concept/Macro 之间缺少的层级关系 ---")
    # Check if sector names contain each other, suggesting hierarchy
    parent_types = ("sector", "concept", "macro_theme")
    hierarchy_candidates = []
    for i, a in enumerate(nodes):
        if a["node_type"] not in parent_types:
            continue
        a_name = a["name"].strip().lower()
        if len(a_name) < 3:
            continue
        for j, b in enumerate(nodes):
            if i >= j:
                continue
            if b["node_type"] not in parent_types:
                continue
            b_name = b["name"].strip().lower()
            if len(b_name) < 3:
                continue

            # One contains the other
            if a_name in b_name and a_name != b_name:
                # b contains a, so a might be parent of b or vice versa
                longer, shorter = (b, a) if len(b_name) > len(a_name) else (a, b)
                if longer["parent_node_id"] is None:
                    hierarchy_candidates.append((longer, shorter, "name_contains"))
            elif b_name in a_name and a_name != b_name:
                longer, shorter = (a, b) if len(a_name) > len(b_name) else (b, a)
                if longer["parent_node_id"] is None:
                    hierarchy_candidates.append((longer, shorter, "name_contains"))

    if hierarchy_candidates:
        seen = set()
        unique = []
        for child, parent, reason in hierarchy_candidates:
            key = (child["id"], parent["id"])
            if key not in seen:
                seen.add(key)
                unique.append((child, parent, reason))
        unique.sort(key=lambda x: x[0]["name"])
        for child, parent, reason in unique[:30]:
            print(f"  [{child['node_type']}] '{child['name']}' → 建议父节点: [{parent['node_type']}] '{parent['name']}'")
        if len(unique) > 30:
            print(f"  ... 共 {len(unique)} 条建议")
    else:
        print("  无")

    # 2e. 检查 sector 节点是否有对应的概念/行业
    print("\n--- 2e. 有parent的节点统计 ---")
    has_parent = sum(1 for n in nodes if n["parent_node_id"])
    no_parent = sum(1 for n in nodes if not n["parent_node_id"])
    print(f"  有父节点: {has_parent}")
    print(f"  无父节点: {no_parent}")

    # Parent type distribution
    print("\n--- 2f. 各类型的 parent_node_id 填充率 ---")
    type_stats = defaultdict(lambda: {"total": 0, "has_parent": 0})
    for n in nodes:
        type_stats[n["node_type"]]["total"] += 1
        if n["parent_node_id"]:
            type_stats[n["node_type"]]["has_parent"] += 1
    for nt, stats in sorted(type_stats.items()):
        pct = stats["has_parent"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"  {nt}: {stats['has_parent']}/{stats['total']} ({pct:.1f}%)")

    # =====================================================================
    # 3. 树形结构检查
    # =====================================================================
    print()
    print("=" * 80)
    print("第三部分: 树形层级结构概览")
    print("=" * 80)

    # Build tree: parent -> children
    parent_to_children = defaultdict(list)
    roots = []
    for n in nodes:
        if n["parent_node_id"]:
            parent_to_children[n["parent_node_id"]].append(n)
        else:
            roots.append(n)

    print(f"\n根节点 (无父节点): {len(roots)}")
    for r in sorted(roots, key=lambda x: x["name"])[:20]:
        print(f"  [{r['node_type']}] '{r['name']}'")
    if len(roots) > 20:
        print(f"  ... 还有 {len(roots) - 20} 个")

    # Check tree depth
    def get_depth(node_id, visited=None):
        if visited is None:
            visited = set()
        if node_id in visited:
            return 0  # cycle detection
        visited.add(node_id)
        node = node_by_id.get(node_id)
        if not node or not node["parent_node_id"]:
            return 1
        return 1 + get_depth(node["parent_node_id"], visited)

    max_depth = 0
    deepest_node = None
    for n in nodes:
        d = get_depth(n["id"])
        if d > max_depth:
            max_depth = d
            deepest_node = n

    print(f"\n最大树深度: {max_depth}")
    if deepest_node:
        # Print path to root
        path = []
        current = deepest_node
        while current:
            path.append(f"[{current['node_type']}] '{current['name']}'")
            if current["parent_node_id"] and current["parent_node_id"] in node_by_id:
                current = node_by_id[current["parent_node_id"]]
            else:
                break
        print(f"最深路径: {' ← '.join(path)}")

    # Count children per parent
    child_counts = [(node_by_id[pid], len(children)) for pid, children in parent_to_children.items() if pid in node_by_id]
    child_counts.sort(key=lambda x: -x[1])
    print(f"\n子节点最多的前10个父节点:")
    for parent, count in child_counts[:10]:
        print(f"  [{parent['node_type']}] '{parent['name']}' → {count} 个子节点")

    # =====================================================================
    # Summary
    # =====================================================================
    print()
    print("=" * 80)
    print("总结")
    print("=" * 80)
    print(f"总节点: {len(nodes)}")
    print(f"同名重复组 (1a): {len(dup_by_exact_name)}")
    print(f"名称包含关关系对 (1b): {len(contained_pairs)}")
    print(f"高相似对 (1c, 同类型): {len(high_sim_pairs)}")
    print(f"同ticker重复 (1d): {len(dup_tickers)}")
    print(f"跨类型相似 (1e): {len(cross_type_pairs)}")
    print(f"孤儿节点 (2a): {orphan_count}")
    print(f"公司缺父节点 (2b): {len(companies_without_parent)}")
    print(f"描述含板块名但缺链接 (2c): {len(unique_links) if potential_missing_links else 0}")
    print(f"层级关系缺失 (2d): {len(unique) if hierarchy_candidates else 0}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
