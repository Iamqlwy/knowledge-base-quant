"""
Phase 1.1: Scan all active world_nodes for duplicate/similar pairs.
Outputs JSONLines to stdout (one pair per line) and a JSON file.

Usage:
    uv run python nodes/phase1_scan.py [--output pairs.json]

Detection strategies:
  1. Exact name + same type
  2. Same cleaned name (after suffix strip), different types (sector vs concept)
  3. High similarity same type (SequenceMatcher >= 0.80)
  4. Name containment (cleaned_name is substring of another)
  5. Same ticker (company nodes with same ticker)
"""
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from sqlalchemy import text
from nodes.common import (
    normalized_name,
    get_engine, load_maintenance_state, read_session,
    text_similarity,
)

SIM_THRESHOLD = 0.80      # Strategy 3: name similarity
SIM_SUFFIX = 0.80         # Strategy 2: cross-type cleaned name

NAME_LEN_RATIO_MIN = 0.55   # length ratio pre-filter for name similarity


async def fetch_nodes(sf):
    """Fetch all active world_nodes with essential fields and counts."""
    async with sf() as session:
        result = await session.execute(
            text("""SELECT id, name, node_type, description, ticker, aliases, created_at
                    FROM world_nodes WHERE is_active = true ORDER BY node_type, name""")
        )
        rows = result.fetchall()

        edge_counts = {}
        edge_result = await session.execute(
            text("""SELECT n.id, COUNT(e.id)
                    FROM world_nodes n
                    LEFT JOIN world_node_edges e ON n.id = e.parent_node_id OR n.id = e.child_node_id
                    WHERE n.is_active = true
                    GROUP BY n.id""")
        )
        for row in edge_result.fetchall():
            edge_counts[str(row[0])] = row[1] or 0

        state_counts = {}
        state_result = await session.execute(
            text("""SELECT node_id, COUNT(id)
                    FROM node_states WHERE effective_to IS NULL
                    GROUP BY node_id""")
        )
        for row in state_result.fetchall():
            state_counts[str(row[0])] = row[1] or 0

        att_counts = {}
        att_result = await session.execute(
            text("""SELECT node_id, COUNT(id)
                    FROM node_attachments
                    GROUP BY node_id""")
        )
        for row in att_result.fetchall():
            att_counts[str(row[0])] = row[1] or 0

    nodes = []
    for row in rows:
        nid = str(row[0])
        nodes.append({
            "id": nid,
            "name": row[1],
            "node_type": row[2],
            "description": row[3] or "",
            "ticker": row[4],
            "aliases": list(row[5]) if row[5] else [],
            "created_at": row[6].isoformat() if row[6] else None,
            "edge_count": edge_counts.get(nid, 0),
            "state_count": state_counts.get(nid, 0),
            "attachment_count": att_counts.get(nid, 0),
        })
    return nodes


def find_duplicates(nodes: list[dict], new_node_ids: set[str] | None = None) -> list[dict]:
    """Run all detection strategies, return deduplicated pair list.

    Strategies 1/2/5 are global (group-by) and remain O(n).
    Strategies 3/4 are combined into a single pairwise pass.
    """
    merges_raw: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    def _add_pair(n0, n1, reason, similarity):
        ids = sorted([n0["id"], n1["id"]])
        key = (ids[0], ids[1])
        if key in seen_pairs:
            return
        if new_node_ids and not ({ids[0], ids[1]} & new_node_ids):
            return
        seen_pairs.add(key)
        merges_raw.append({
            "reason": reason,
            "similarity": similarity,
            "nodes": [
                {"id": n0["id"], "name": n0["name"],
                 "node_type": n0["node_type"],
                 "description": n0["description"],
                 "aliases": n0["aliases"],
                 "ticker": n0["ticker"],
                 "created_at": n0["created_at"],
                 "edge_count": n0.get("edge_count", 0),
                 "state_count": n0.get("state_count", 0),
                 "attachment_count": n0.get("attachment_count", 0)},
                {"id": n1["id"], "name": n1["name"],
                 "node_type": n1["node_type"],
                 "description": n1["description"],
                 "aliases": n1["aliases"],
                 "ticker": n1["ticker"],
                 "created_at": n1["created_at"],
                 "edge_count": n1.get("edge_count", 0),
                 "state_count": n1.get("state_count", 0),
                 "attachment_count": n1.get("attachment_count", 0)},
            ],
        })

    # Pre-compute all derived fields once:
    #   _raw_name = lowercased original name (for exact match)
    #   _cname    = noise-stripped + suffix-cleaned + lowercased (for sim/containment)
    for n in nodes:
        raw = n["name"].strip()
        n["_raw_name"] = raw.lower()
        n["_cname"] = normalized_name(raw)
        n["_cname_len"] = len(n["_cname"])

    # -- Strategy 1: Exact name + same type --
    print("  [策略1] 名称精确匹配...", file=sys.stderr)
    name_type_groups = defaultdict(list)
    for n in nodes:
        name_type_groups[(n["_raw_name"], n["node_type"])].append(n)
    for group in name_type_groups.values():
        if len(group) > 1:
            group.sort(key=lambda x: len(x["description"]), reverse=True)
            for i in range(1, len(group)):
                _add_pair(group[0], group[i], "exact_name_type", 1.0)
    print(f"  [策略1] {len(merges_raw)} pairs so far", file=sys.stderr)

    # Strategy 2: pair sectors and concepts whose names match after noise-stripping
    # + suffix-stripping.  "华峰化学板块" (sector) vs "华峰化学概念" (concept) →
    # stripped of noise words then cleaned → both "华峰化学".
    sectors = [n for n in nodes if n["node_type"] == "sector"]
    concepts = [n for n in nodes if n["node_type"] == "concept"]
    print(f"  [策略2] 去噪跨类型 (sectors={len(sectors)} concepts={len(concepts)})...", file=sys.stderr)

    # Dict for exact normalized-name match
    sector_by_nname: dict[str, list] = defaultdict(list)
    for s in sectors:
        if s["_cname_len"] >= 3:
            sector_by_nname[s["_cname"]].append(s)

    for c in concepts:
        cname = c["_cname"]
        clen = c["_cname_len"]
        if clen < 3:
            continue
        # Exact match on fully normalized name (noise stripped + suffix cleaned)
        for s in sector_by_nname.get(cname, []):
            _add_pair(s, c, "suffix_match_exact", 1.0)
        # Fuzzy: compare noise-stripped names with length pre-filter
        for s in sectors:
            slen = s["_cname_len"]
            if slen < 3:
                continue
            if s["_cname"] == cname:
                continue  # already exact-matched above
            if min(clen, slen) / max(clen, slen) < NAME_LEN_RATIO_MIN:
                continue
            # Use normalized name (noise stripped + cleaned) for similarity
            sim = text_similarity(cname, s["_cname"])
            if sim >= SIM_SUFFIX:
                _add_pair(s, c, "suffix_match_sim", round(sim, 3))
    print(f"  [策略2] {len(merges_raw)} pairs so far", file=sys.stderr)

    # -- Determine sets for pairwise strategies --
    if new_node_ids:
        anchors = [n for n in nodes if n["id"] in new_node_ids]
        others = nodes
    else:
        anchors = nodes
        others = nodes
    print(f"  anchors={len(anchors)}(新节点)  candidates={len(others)}(全部)", file=sys.stderr)

    # -- Combined pairwise pass: Strategies 3, 4 --
    print("  [策略3+4] 成对扫描中...", file=sys.stderr)
    pair_before = len(merges_raw)
    s3_count = s4_count = 0
    scanned = 0
    total_pairs = len(anchors) * (len(others) - 1)

    for a in anchors:
        aname = a["_cname"]
        aname_len = a["_cname_len"]
        atype = a["node_type"]
        aid = a["id"]

        for b in others:
            if aid == b["id"]:
                continue
            scanned += 1

            bname = b["_cname"]
            bname_len = b["_cname_len"]
            btype = b["node_type"]

            if aname_len < 3 and bname_len < 3:
                continue

            # -- Strategy 3: High name similarity, same type --
            if (atype == btype and aname_len >= 4 and bname_len >= 4
                    and aname != bname
                    and min(aname_len, bname_len) / max(aname_len, bname_len) >= NAME_LEN_RATIO_MIN):
                sm = SequenceMatcher(None, aname, bname)
                if sm.real_quick_ratio() >= 0.70:
                    sim = sm.ratio()
                    if sim >= SIM_THRESHOLD:
                        _add_pair(a, b, "high_sim_same_type", round(sim, 3))
                        s3_count += 1
                        continue

            # -- Strategy 4: Name containment (both directions) --
            if aname_len >= 3 and bname_len >= 3 and aname != bname:
                if aname in bname and bname_len > aname_len:
                    sim = aname_len / bname_len
                    _add_pair(a, b, "name_contains", round(sim, 3))
                    s4_count += 1
                    continue
                if bname in aname and aname_len > bname_len:
                    sim = bname_len / aname_len
                    _add_pair(a, b, "name_contains", round(sim, 3))
                    s4_count += 1
                    continue

        if scanned % 100000 == 0:
            pct = scanned * 100 // total_pairs if total_pairs else 0
            print(f"    已扫描 {scanned}/{total_pairs} 对 ({pct}%)...", file=sys.stderr)

    total_new = len(merges_raw) - pair_before
    print(f"  [策略3] +{s3_count}  [策略4] +{s4_count}", file=sys.stderr)
    print(f"  成对策略共新增 {total_new} pairs", file=sys.stderr)

    # -- Strategy 5: Same ticker company --
    print("  [策略5] 相同ticker...", file=sys.stderr)
    s5_before = len(merges_raw)
    ticker_groups = defaultdict(list)
    for n in nodes:
        if n.get("ticker") and n["node_type"] == "company":
            ticker_groups[n["ticker"]].append(n)
    for ticker, group in ticker_groups.items():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    sim = text_similarity(group[i]["name"], group[j]["name"])
                    if sim >= 0.7:
                        _add_pair(group[i], group[j], f"same_ticker_{ticker}", round(sim, 3))
    print(f"  [策略5] +{len(merges_raw) - s5_before} pairs", file=sys.stderr)

    return merges_raw


async def main():
    output_file = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    engine = get_engine()
    sf = read_session(engine)

    state = load_maintenance_state()
    last_run = state.get("last_run", "2026-03-31T00:00:00+08:00")
    try:
        last_run_dt = datetime.fromisoformat(last_run)
    except ValueError:
        last_run_dt = datetime(2026, 3, 31, tzinfo=timezone.utc)
    print(f"上次维护时间: {last_run[:10]}", file=sys.stderr)

    print("正在加载活跃节点...", file=sys.stderr)
    nodes = await fetch_nodes(sf)
    await engine.dispose()

    new_node_ids: set[str] = set()
    for n in nodes:
        if not n["created_at"]:
            continue
        try:
            created_dt = datetime.fromisoformat(n["created_at"])
        except ValueError:
            continue
        if created_dt > last_run_dt:
            new_node_ids.add(n["id"])
    print(f"已加载 {len(nodes)} 个活跃节点 (其中 {len(new_node_ids)} 个在上次维护后创建)", file=sys.stderr)

    by_type = defaultdict(int)
    for n in nodes:
        by_type[n["node_type"]] += 1
    for nt, count in sorted(by_type.items()):
        print(f"  {nt}: {count}", file=sys.stderr)

    print(f"\n开始检测...", file=sys.stderr)
    print(f"  策略: 1-名称精确匹配  2-去后缀跨类型  3-高相似度({SIM_THRESHOLD})  4-名称包含", file=sys.stderr)
    print(f"  策略: 5-相同ticker", file=sys.stderr)
    pairs = find_duplicates(nodes, new_node_ids)

    print(f"\n发现 {len(pairs)} 对候选", file=sys.stderr)

    reason_counts = defaultdict(int)
    for p in pairs:
        reason_counts[p["reason"]] += 1
    for reason, count in sorted(reason_counts.items()):
        print(f"  {reason}: {count}", file=sys.stderr)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(pairs, f, ensure_ascii=False, indent=2)
        print(f"\n已写入: {output_file}", file=sys.stderr)
    else:
        for i, pair in enumerate(pairs):
            pair["pair_id"] = f"dup_{i+1:04d}"
            print(json.dumps(pair, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
