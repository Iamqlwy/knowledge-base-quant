"""深入分析 world_nodes 的重复和缺失关系问题，输出详细报告。"""
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
    node_by_id = {n["id"]: n for n in nodes}

    print(f"## 总节点数: {len(nodes)}")
    print()

    # =====================================================================
    # 1. SECTOR vs CONCEPT 同名/近似问题
    # =====================================================================
    print("=" * 80)
    print("第一部分: Sector 与 Concept 节点重叠/重复问题")
    print("=" * 80)

    sectors = [n for n in nodes if n["node_type"] == "sector"]
    concepts = [n for n in nodes if n["node_type"] == "concept"]

    sector_by_name = {}
    for s in sectors:
        name = s["name"].strip()
        sector_by_name[name] = s
        # Also index without 板块 suffix
        if name.endswith("板块"):
            sector_by_name[name[:-2].strip()] = s
        if name.endswith("概念"):
            sector_by_name[name[:-2].strip()] = s

    overlaps = []
    for c in concepts:
        cname = c["name"].strip()
        # Direct match
        if cname in sector_by_name:
            overlaps.append((c, sector_by_name[cname], "exact_sans_suffix", 1.0))
            continue
        # Concept name + "板块" matches a sector
        if cname + "板块" in sector_by_name:
            overlaps.append((c, sector_by_name[cname + "板块"], "concept_plus_板块", 0.95))
            continue
        # High similarity
        for sname, s in sector_by_name.items():
            if "板块" in sname or "概念" in sname:
                continue  # skip already indexed
            sim = SequenceMatcher(None, cname.lower(), sname.lower()).ratio()
            if sim >= 0.85:
                overlaps.append((c, s, f"similar_{sim:.2f}", sim))
                break

    if overlaps:
        print()
        for concept, sector, reason, score in overlaps:
            print(f"  [{concept['node_type']}] '{concept['name']}'")
            print(f"     vs [{sector['node_type']}] '{sector['name']}' (reason={reason})")
            print(f"     concept:  id={concept['id']} desc={concept['description'][:80]}")
            print(f"     sector:   id={sector['id']} desc={sector['description'][:80]}")
            # Check if either has a parent
            c_parent = f"parent={concept['parent_node_id']}" if concept["parent_node_id"] else "no parent"
            s_parent = f"parent={sector['parent_node_id']}" if sector["parent_node_id"] else "no parent"
            print(f"     concept {c_parent} | sector {s_parent}")
            print()
        print(f"  --- 共 {len(overlaps)} 组重叠 ---")
    else:
        print("  未发现明显重叠")

    # =====================================================================
    # 2. SECTOR 名称包含关系 (板块嵌套)
    # =====================================================================
    print()
    print("=" * 80)
    print("第二部分: Sector 之间的潜在层级关系")
    print("=" * 80)

    # Find sectors where one name contains another
    sector_nesting = []
    for i, a in enumerate(sectors):
        aname = a["name"].strip()
        if len(aname) < 3:
            continue
        for j, b in enumerate(sectors):
            if i >= j:
                continue
            bname = b["name"].strip()
            if len(bname) < 3:
                continue
            if aname != bname and (aname in bname or bname in aname):
                longer = a if len(aname) > len(bname) else b
                shorter = b if longer is a else a
                if longer["parent_node_id"] is None:
                    sector_nesting.append((longer, shorter))

    if sector_nesting:
        seen = set()
        for child, parent in sector_nesting:
            key = (child["id"], parent["id"])
            if key in seen:
                continue
            seen.add(key)
            print(f"  '{child['name']}' → 可考虑父节点: '{parent['name']}'")
            print(f"    child:  id={child['id']} parent={child['parent_node_id']}")
            print(f"    parent: id={parent['id']}")
        print(f"  --- 共 {len(seen)} 组建议 ---")
    else:
        print("  无")

    # =====================================================================
    # 3. CONCEPT 之间的潜在层级关系
    # =====================================================================
    print()
    print("=" * 80)
    print("第三部分: Concept 之间的潜在层级关系")
    print("=" * 80)

    concept_nesting = []
    for i, a in enumerate(concepts):
        aname = a["name"].strip()
        if len(aname) < 4:
            continue
        for j, b in enumerate(concepts):
            if i >= j:
                continue
            bname = b["name"].strip()
            if len(bname) < 4:
                continue
            if aname != bname and (aname in bname or bname in aname):
                longer = a if len(aname) > len(bname) else b
                shorter = b if longer is a else a
                if longer["parent_node_id"] is None:
                    concept_nesting.append((longer, shorter))

    if concept_nesting:
        seen = set()
        for child, parent in concept_nesting:
            key = (child["id"], parent["id"])
            if key in seen:
                continue
            seen.add(key)
            print(f"  '{child['name']}' → 可考虑父节点: '{parent['name']}'")
            print(f"    child:  id={child['id']} parent={child['parent_node_id']}")
            print(f"    parent: id={parent['id']}")
        print(f"  --- 共 {len(seen)} 组建议 ---")
    else:
        print("  无")

    # =====================================================================
    # 4. 重复/高相似 company 节点
    # =====================================================================
    print()
    print("=" * 80)
    print("第四部分: 可能重复的 Company 节点")
    print("=" * 80)

    companies = [n for n in nodes if n["node_type"] == "company"]
    company_dups = []

    for i, a in enumerate(companies):
        aname = a["name"].strip().lower()
        for j, b in enumerate(companies):
            if i >= j:
                continue
            bname = b["name"].strip().lower()
            sim = SequenceMatcher(None, aname, bname).ratio()
            if sim >= 0.92:
                company_dups.append((a, b, sim))

    company_dups.sort(key=lambda x: -x[2])
    if company_dups:
        for a, b, sim in company_dups[:20]:
            print(f"  sim={sim:.3f}: '{a['name']}' ↔ '{b['name']}'")
            print(f"    A: id={a['id']} ticker={a['ticker']} parent={a['parent_node_id']}")
            print(f"    B: id={b['id']} ticker={b['ticker']} parent={b['parent_node_id']}")
            print(f"    A desc: {a['description'][:100]}")
            print(f"    B desc: {b['description'][:100]}")
            print()
        if len(company_dups) > 20:
            print(f"  ... 共 {len(company_dups)} 组")
    else:
        print("  无")

    # =====================================================================
    # 5. 树结构深度分析
    # =====================================================================
    print()
    print("=" * 80)
    print("第五部分: 树结构详细分析")
    print("=" * 80)

    # 根节点分类
    roots = [n for n in nodes if n["parent_node_id"] is None]
    print(f"\n根节点 ({len(roots)}):")
    for nt in sorted(set(n["node_type"] for n in roots)):
        count = sum(1 for n in roots if n["node_type"] == nt)
        print(f"  {nt}: {count}")

    # 所有 sector 节点的父子关系
    print(f"\n所有 sector 节点 ({len(sectors)}):")
    for s in sectors:
        children = [n for n in nodes if n["parent_node_id"] == s["id"]]
        parent_info = ""
        if s["parent_node_id"] and s["parent_node_id"] in node_by_id:
            parent_info = f" → parent: [{node_by_id[s['parent_node_id']]['node_type']}] '{node_by_id[s['parent_node_id']]['name']}'"
        elif s["parent_node_id"]:
            parent_info = f" → parent: (missing: {s['parent_node_id']})"
        else:
            parent_info = " → (根节点)"
        print(f"  '{s['name']}' children={len(children)}{parent_info}")
        if children:
            for ch in children[:3]:
                print(f"    - [{ch['node_type']}] '{ch['name']}'")
            if len(children) > 3:
                print(f"    ... 还有 {len(children)-3} 个")

    # 所有 macro_theme
    macros = [n for n in nodes if n["node_type"] == "macro_theme"]
    print(f"\n所有 macro_theme ({len(macros)}):")
    for m in macros:
        children = [n for n in nodes if n["parent_node_id"] == m["id"]]
        print(f"  '{m['name']}' children={len(children)} parent={m['parent_node_id']}")

    # =====================================================================
    # 6. 综合问题总结
    # =====================================================================
    print()
    print("=" * 80)
    print("第六部分: 综合问题清单")
    print("=" * 80)

    issues = []

    # Issue type 1: Sector and Concept describe the same thing
    for concept, sector, reason, score in overlaps:
        issues.append({
            "type": "SECTOR_CONCEPT_OVERLAP",
            "severity": "HIGH",
            "description": f"[{concept['node_type']}] '{concept['name']}' 与 [{sector['node_type']}] '{sector['name']}' 描述同一事物",
            "concept_id": concept["id"],
            "sector_id": sector["id"],
            "recommendation": "合并节点或建立父子/等价关系"
        })

    # Issue type 2: High similarity nodes
    for a, b, sim in company_dups:
        issues.append({
            "type": "HIGH_SIMILARITY",
            "severity": "MEDIUM",
            "description": f"[{a['node_type']}] '{a['name']}' ↔ '{b['name']}' (sim={sim:.3f})",
            "node_a_id": a["id"],
            "node_b_id": b["id"],
            "recommendation": "检查是否为重复节点，考虑合并"
        })

    # Issue type 3: Missing parent links (sector nesting)
    for child, parent in sector_nesting:
        key = (child["id"], parent["id"])
        issues.append({
            "type": "MISSING_PARENT_SECTOR",
            "severity": "MEDIUM",
            "description": f"[{child['node_type']}] '{child['name']}' 应设父节点为 [{parent['node_type']}] '{parent['name']}'",
            "child_id": child["id"],
            "parent_id": parent["id"],
        })

    # Issue type 4: Missing parent links (concept nesting)
    for child, parent in concept_nesting:
        key = (child["id"], parent["id"])
        issues.append({
            "type": "MISSING_PARENT_CONCEPT",
            "severity": "MEDIUM",
            "description": f"[{child['node_type']}] '{child['name']}' 应设父节点为 [{parent['node_type']}] '{parent['name']}'",
            "child_id": child["id"],
            "parent_id": parent["id"],
        })

    print(f"\n共发现 {len(issues)} 个问题\n")

    for i, iss in enumerate(issues):
        print(f"  {i+1}. [{iss['severity']}] {iss['type']}")
        print(f"     {iss['description']}")
        if "recommendation" in iss:
            print(f"     建议: {iss['recommendation']}")
        print()

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
