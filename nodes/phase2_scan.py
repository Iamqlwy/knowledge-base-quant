"""
Phase 2: Edge candidate discovery and resolution.

Step 2.1: Extract nodes created after last maintenance.
Step 2.2: For each new node, use multi-signal scoring to find candidate edges.
Step 2.3: Output candidate edge pairs for agent review.

Signals:
  A. BM25 search: use node description as query against ES nodes index
     Falls back to PG ILIKE if ES nodes index is unavailable.
  B. Text similarity: name/name + description/description + description vs node_state fields
  C. Name containment: cleaned name substring matching

Usage:
    uv run python nodes/phase2_scan.py [--output candidates.json]
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from sqlalchemy import text
from nodes.common import (
    clean_name, text_similarity, keyword_overlap_score,
    load_maintenance_state, get_engine, read_session,
)

# Weights for multi-signal scoring
W_BM25 = 0.35
W_NAME_SIM = 0.15
W_DESC_SIM = 0.15
W_STATE_OVERLAP = 0.20
W_NAME_CONTAIN = 0.15

# Minimum composite score to consider as candidate
MIN_CANDIDATE_SCORE = 0.15

# Max candidates per new node
MAX_CANDIDATES_PER_NODE = 5

# Node types that can be connected (parent_type, child_type, default_edge_type)
TYPE_PAIR_RULES: dict[tuple[str, str], str] = {
    ("company", "sector"): "belongs_to",
    ("sector", "macro_theme"): "belongs_to",
    ("sector", "sector"): "belongs_to",
    ("concept", "sector"): "classified_as",
    ("sector", "concept"): "belongs_to",
    ("company", "product"): "has_business_segment",
    ("company", "company"): "competes_in",
    ("company", "person"): "led_by",
    ("institution", "person"): "led_by",
    ("person", "institution"): "affiliated_with",
    ("company", "region"): "based_in",
    ("institution", "region"): "based_in",
    ("policy", "sector"): "regulated_by",
    ("sector", "policy"): "regulated_by",
    ("policy", "company"): "regulated_by",
    ("macro_theme", "sector"): "belongs_to",
    ("concept", "concept"): "belongs_to",
}


def default_edge_type(parent_type: str, child_type: str) -> str | None:
    return TYPE_PAIR_RULES.get((parent_type, child_type))


async def fetch_all_nodes(sf):
    async with sf() as session:
        result = await session.execute(
            text("""SELECT id, name, node_type, description, ticker, aliases, created_at
                    FROM world_nodes WHERE is_active = true ORDER BY node_type, name""")
        )
        rows = result.fetchall()

    nodes = []
    for row in rows:
        nodes.append({
            "id": str(row[0]),
            "name": row[1],
            "node_type": row[2],
            "description": row[3] or "",
            "ticker": row[4],
            "aliases": list(row[5]) if row[5] else [],
            "created_at": row[6],
        })
    return nodes


async def fetch_current_states(sf, node_ids: list[str]) -> dict[str, dict]:
    if not node_ids:
        return {}
    # Batch in chunks of 500 to avoid too-large IN clauses
    states = {}
    for chunk in _chunk(node_ids, 500):
        ids_str = ", ".join(f"'{nid}'" for nid in chunk)
        async with sf() as session:
            result = await session.execute(
                text(f"""SELECT node_id, core_logic, state_summary, primary_drivers, recent_changes
                         FROM node_states
                         WHERE node_id IN ({ids_str}) AND effective_to IS NULL""")
            )
            for row in result.fetchall():
                states[str(row[0])] = {
                    "core_logic": row[1] or "",
                    "state_summary": row[2] or "",
                    "primary_drivers": json.dumps(row[3], ensure_ascii=False) if row[3] else "",
                    "recent_changes": row[4] or "",
                }
    return states


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


async def fetch_existing_edges(sf, node_ids: list[str]) -> set[tuple[str, str]]:
    if not node_ids:
        return set()
    edges = set()
    for chunk in _chunk(node_ids, 500):
        ids_str = ", ".join(f"'{nid}'" for nid in chunk)
        async with sf() as session:
            result = await session.execute(
                text(f"""SELECT parent_node_id, child_node_id
                         FROM world_node_edges
                         WHERE parent_node_id IN ({ids_str}) OR child_node_id IN ({ids_str})""")
            )
            for row in result.fetchall():
                edges.add((str(row[0]), str(row[1])))
    return edges


# -- BM25 search: ES first, PG fallback --

async def _es_search_node(query_text: str, limit: int) -> list[dict] | None:
    try:
        from kbquant.integrations.elasticsearch.client import get_es
        from kbquant.integrations.elasticsearch.index_registry import PREFIX
    except ImportError:
        return None

    try:
        es = get_es()
        index_name = f"{PREFIX}_nodes"
        body = {
            "query": {
                "bool": {
                    "must": [{"multi_match": {
                        "query": query_text,
                        "fields": ["name^2", "description", "node_type"],
                    }}],
                },
            },
            "size": limit,
        }
        resp = await es.search(index=index_name, body=body)
        hits = resp["hits"]["hits"]
        results = []
        for h in hits:
            src = h["_source"]
            results.append({
                "id": src.get("pg_id", h["_id"]),
                "name": src.get("name", ""),
                "node_type": src.get("node_type", ""),
                "description": src.get("description", ""),
                "bm25_score": h["_score"] or 0.0,
            })
        return results
    except Exception:
        return None


async def _pg_search_node(query_text: str, limit: int) -> list[dict]:
    keywords = [w.strip() for w in query_text.split() if len(w.strip()) >= 2][:10]
    if not keywords:
        return []

    engine = get_engine()
    sf = read_session(engine)
    results: list[dict] = []

    async with sf() as session:
        for kw in keywords[:5]:
            r = await session.execute(
                text("""SELECT id, name, node_type, description
                        FROM world_nodes WHERE is_active = true
                          AND (name ILIKE :pat OR description ILIKE :pat)
                        LIMIT :lim"""),
                {"pat": f"%{kw}%", "lim": limit},
            )
            for row in r.fetchall():
                nid = str(row[0])
                if not any(rr["id"] == nid for rr in results):
                    results.append({
                        "id": nid,
                        "name": row[1],
                        "node_type": row[2],
                        "description": row[3] or "",
                        "bm25_score": 5.0,
                    })
            if len(results) >= limit:
                break

    await engine.dispose()
    return results[:limit]


async def bm25_search_node(query_text: str, limit: int = 10) -> list[dict]:
    results = await _es_search_node(query_text, limit)
    if results is not None:
        return results
    return await _pg_search_node(query_text, limit)


# -- Multi-signal scoring --

def compute_composite_score(new_node: dict, candidate: dict,
                            new_state: dict | None,
                            candidate_state: dict | None) -> dict:
    # Signal A: BM25 (normalized)
    bm25 = min(candidate.get("bm25_score", 0.0) / 20.0, 1.0)

    # Signal B: Name similarity
    name_sim = text_similarity(
        new_node.get("_cname", clean_name(new_node["name"])),
        candidate.get("_cname", clean_name(candidate["name"])),
    )

    # Signal B2: Description similarity
    desc_sim = text_similarity(
        new_node.get("description", ""),
        candidate.get("description", ""),
    )

    # Signal B3: Description vs node_state fields
    new_desc = new_node.get("description", "") or ""
    candidate_desc = candidate.get("description", "") or ""

    state_text_new = ""
    if new_state:
        state_text_new = " ".join(filter(None, [
            new_state.get("core_logic", ""),
            new_state.get("state_summary", ""),
            new_state.get("primary_drivers", ""),
        ]))

    state_text_candidate = ""
    if candidate_state:
        state_text_candidate = " ".join(filter(None, [
            candidate_state.get("core_logic", ""),
            candidate_state.get("state_summary", ""),
            candidate_state.get("primary_drivers", ""),
        ]))

    desc_vs_state = 0.0
    if state_text_candidate or state_text_new:
        desc_vs_state = max(
            keyword_overlap_score(new_desc, state_text_candidate),
            keyword_overlap_score(candidate_desc, state_text_new),
        )

    # Signal C: Name containment
    nc_new = new_node.get("_cname", clean_name(new_node["name"])).lower()
    nc_cand = candidate.get("_cname", clean_name(candidate["name"])).lower()
    name_contain = 0.0
    if nc_new and nc_cand and nc_new != nc_cand:
        if nc_new in nc_cand or nc_cand in nc_new:
            shorter = min(len(nc_new), len(nc_cand))
            longer = max(len(nc_new), len(nc_cand))
            if longer > 0:
                name_contain = shorter / longer

    composite = (
        W_BM25 * bm25 +
        W_NAME_SIM * name_sim +
        W_DESC_SIM * desc_sim +
        W_STATE_OVERLAP * desc_vs_state +
        W_NAME_CONTAIN * name_contain
    )

    return {
        "composite": round(composite, 4),
        "breakdown": {
            "bm25": round(bm25, 4),
            "name_sim": round(name_sim, 4),
            "desc_sim": round(desc_sim, 4),
            "desc_vs_state": round(desc_vs_state, 4),
            "name_contain": round(name_contain, 4),
        },
    }


# -- Name containment candidate pre-filter --
# Build reverse-length index: for a given name length, which other nodes
# have a longer cleaned name? This avoids scanning all nodes per anchor.

def _build_containment_candidates(new_nodes, all_nodes, connected):
    """Pre-compute containment candidate lists for each new node.
    Returns {nid: [other_node, ...]} for nodes whose name could contain anchor's name.
    Only includes nodes with compatible types per TYPE_PAIR_RULES."""
    # Pre-compute cleaned names for all nodes
    for n in all_nodes:
        n["_cname"] = clean_name(n["name"]).lower()

    # Group all_nodes by cleaned-name length bucket (floor to nearest power of 2)
    # so we only check longer names for containment
    result = {}
    for nn in new_nodes:
        nid = nn["id"]
        nc = nn.get("_cname", clean_name(nn["name"])).lower()
        if len(nc) < 3:
            continue
        node_conn = connected.get(nid, set())
        candidates = []
        for other in all_nodes:
            oid = other["id"]
            if oid == nid:
                continue
            if oid in node_conn:
                continue
            oc = other["_cname"]
            if len(oc) < 3:
                continue
            if nc == oc:
                continue
            # Only accept containment, not equality
            if nc in oc or oc in nc:
                # Check type pair has a rule
                ntype = nn["node_type"]
                ctype = other["node_type"]
                if (ntype, ctype) in TYPE_PAIR_RULES or (ctype, ntype) in TYPE_PAIR_RULES:
                    candidates.append(other)
        result[nid] = candidates
    return result


# -- Main --

async def main():
    output_file = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    state = load_maintenance_state()
    last_run = state.get("last_run", "2026-03-31T00:00:00+08:00")
    print(f"上次维护时间: {last_run}", file=sys.stderr)

    engine = get_engine()
    sf = read_session(engine)

    all_nodes = await fetch_all_nodes(sf)

    try:
        last_run_dt = datetime.fromisoformat(last_run)
    except ValueError:
        last_run_dt = datetime(2026, 3, 31, tzinfo=timezone.utc)

    new_nodes = [n for n in all_nodes if n["created_at"] and n["created_at"] > last_run_dt]
    print(f"全部节点: {len(all_nodes)}, 新节点 (>{last_run[:10]}): {len(new_nodes)}",
          file=sys.stderr)

    if not new_nodes:
        print("没有新节点需要处理", file=sys.stderr)
        await engine.dispose()
        return

    # Fetch existing edges
    all_ids = [n["id"] for n in all_nodes]
    existing_edges = await fetch_existing_edges(sf, all_ids)

    connected: dict[str, set[str]] = {}
    for (p, c) in existing_edges:
        connected.setdefault(p, set()).add(c)
        connected.setdefault(c, set()).add(p)

    await engine.dispose()

    all_index = {n["id"]: n for n in all_nodes}

    # Pre-compute cleaned names on all nodes (once)
    for n in all_nodes:
        n["_cname"] = clean_name(n["name"]).lower()

    # Filter: skip new nodes that already have edges
    new_nodes_no_edge = [n for n in new_nodes if not connected.get(n["id"])]

    # Build containment candidate index
    print("  预计算名称包含候选...", file=sys.stderr)
    containment_map = _build_containment_candidates(new_nodes_no_edge, all_nodes, connected)

    # -- Phase A: Run all BM25 searches in parallel --
    print(f"  并行执行 {len(new_nodes_no_edge)} 个BM25搜索...", file=sys.stderr)
    bm25_results_map: dict[str, list[dict]] = {}

    async def _search_one(nn):
        query = f"{nn['name']} {nn['description']}"[:500]
        return nn["id"], await bm25_search_node(query, limit=15)

    # Run in batches of 10 to avoid overwhelming ES/PG
    for batch in _chunk(new_nodes_no_edge, 10):
        batch_results = await asyncio.gather(*[_search_one(nn) for nn in batch])
        for nid, results in batch_results:
            bm25_results_map[nid] = results

    # -- Phase B: Fetch node_states for scoring --
    # Collect all node IDs that appear in BM25 results (plus new nodes themselves)
    state_ids: set[str] = set()
    for nid, bm_results in bm25_results_map.items():
        state_ids.add(nid)
        for bm in bm_results:
            if bm.get("id"):
                state_ids.add(bm["id"])
    # Also add containment candidates
    for nid, cands in containment_map.items():
        state_ids.add(nid)
        for c in cands:
            state_ids.add(c["id"])

    print(f"  获取 {len(state_ids)} 个节点的state数据...", file=sys.stderr)
    all_states = await fetch_current_states(sf, list(state_ids))

    # -- Phase C: Score and collect candidates --
    print("  评分候选...", file=sys.stderr)
    candidates: list[dict] = []
    new_nodes_processed = 0
    new_nodes_with_candidates = 0

    for new_node in new_nodes_no_edge:
        nid = new_node["id"]
        new_state = all_states.get(nid)
        new_nodes_processed += 1

        scored: dict[str, dict] = {}  # candidate_node_id -> candidate entry

        # Process BM25 results
        for bm in bm25_results_map.get(nid, []):
            bid = bm.get("id")
            if not bid or bid == nid:
                continue
            if bid in connected.get(nid, set()):
                continue

            candidate_node = all_index.get(bid)
            if not candidate_node:
                continue

            scores = compute_composite_score(
                new_node,
                {**candidate_node, "bm25_score": bm["bm25_score"],
                 "_cname": candidate_node.get("_cname", "")},
                new_state,
                all_states.get(bid),
            )

            if scores["composite"] >= MIN_CANDIDATE_SCORE:
                ntype = new_node["node_type"]
                ctype = candidate_node["node_type"]
                edge_type = default_edge_type(ntype, ctype)
                if edge_type:
                    parent, child = nid, bid
                else:
                    edge_type = default_edge_type(ctype, ntype)
                    if edge_type:
                        parent, child = bid, nid
                    else:
                        continue

                scored[bid] = {
                    "candidate_node_id": bid,
                    "candidate_name": candidate_node["name"],
                    "candidate_type": ctype,
                    "candidate_description": candidate_node.get("description", "")[:200],
                    "proposed_parent_id": parent,
                    "proposed_child_id": child,
                    "proposed_edge_type": edge_type,
                    "scores": scores,
                }

        # Process containment candidates (name-only, no BM25)
        for other_node in containment_map.get(nid, []):
            oid = other_node["id"]
            if oid in scored:
                continue
            if oid in connected.get(nid, set()):
                continue

            nc_new = new_node["_cname"]
            nc_other = other_node["_cname"]
            name_contain = min(len(nc_new), len(nc_other)) / max(len(nc_new), len(nc_other))
            desc_sim = text_similarity(
                new_node.get("description", ""),
                other_node.get("description", ""),
            )
            composite = W_NAME_CONTAIN * name_contain + W_DESC_SIM * desc_sim

            if composite >= MIN_CANDIDATE_SCORE:
                ntype = new_node["node_type"]
                ctype = other_node["node_type"]
                edge_type = default_edge_type(ntype, ctype)
                if edge_type:
                    parent, child = nid, oid
                else:
                    edge_type = default_edge_type(ctype, ntype)
                    if edge_type:
                        parent, child = oid, nid
                    else:
                        continue

                scored[oid] = {
                    "candidate_node_id": oid,
                    "candidate_name": other_node["name"],
                    "candidate_type": other_node["node_type"],
                    "candidate_description": other_node.get("description", "")[:200],
                    "proposed_parent_id": parent,
                    "proposed_child_id": child,
                    "proposed_edge_type": edge_type,
                    "scores": {
                        "composite": round(composite, 4),
                        "breakdown": {
                            "bm25": 0.0,
                            "name_sim": 0.0,
                            "desc_sim": round(desc_sim, 4),
                            "desc_vs_state": 0.0,
                            "name_contain": round(name_contain, 4),
                        },
                    },
                }

        # Keep top N
        top = sorted(scored.values(), key=lambda x: -x["scores"]["composite"])[:MAX_CANDIDATES_PER_NODE]

        if top:
            new_nodes_with_candidates += 1
            candidates.append({
                "new_node_id": nid,
                "new_node_name": new_node["name"],
                "new_node_type": new_node["node_type"],
                "new_node_description": new_node["description"][:300],
                "candidates": top,
            })

    print(f"处理完成: {new_nodes_processed} 个新节点, "
          f"{new_nodes_with_candidates} 个有新候选边", file=sys.stderr)

    if not candidates:
        print("未发现候选边", file=sys.stderr)
        return

    total_candidates = sum(len(c["candidates"]) for c in candidates)
    print(f"生成 {total_candidates} 个候选边", file=sys.stderr)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(candidates, f, ensure_ascii=False, indent=2)
        print(f"已写入: {output_file}", file=sys.stderr)
    else:
        for c in candidates:
            for cand in c["candidates"]:
                rec = {
                    "edge_id": f"edge_{c['new_node_id'][:8]}_{cand['candidate_node_id'][:8]}",
                    "new_node": {
                        "id": c["new_node_id"],
                        "name": c["new_node_name"],
                        "type": c["new_node_type"],
                        "description": c["new_node_description"],
                    },
                    "candidate_node": {
                        "id": cand["candidate_node_id"],
                        "name": cand["candidate_name"],
                        "type": cand["candidate_type"],
                        "description": cand.get("candidate_description", ""),
                    },
                    "proposed": {
                        "parent_id": cand["proposed_parent_id"],
                        "child_id": cand["proposed_child_id"],
                        "edge_type": cand["proposed_edge_type"],
                    },
                    "scores": cand["scores"],
                }
                print(json.dumps(rec, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            import kbquant.integrations.elasticsearch.client as _es_mod
            es = _es_mod.get_es()
            if es is not None:
                asyncio.run(es.close())
                _es_mod._es_client = None
        except Exception:
            pass
