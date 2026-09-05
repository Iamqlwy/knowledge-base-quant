"""
Phase 1.2: Resolve duplicate pairs — load pair context and execute dedup actions.

Takes a JSON Lines file of pairs (from phase1_scan.py) and processes each pair.
Each pair gets enriched with full context (edges, node_states, attachments counts)
before being handed to the sub-agent for decision.

For now, this script takes a single pair as input (designed to be called by
an orchestrator/agent per pair).

Usage:
    uv run python nodes/phase1_resolve.py <pair_json_line>
    or
    uv run python nodes/phase1_resolve.py --file <pairs.json>
"""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from sqlalchemy import text
from nodes.common import get_engine, write_session, read_session, utcnow


# ── Context enrichment ──────────────────────────────────────────

async def load_pair_context(sf, pair: dict) -> dict:
    """Enrich a pair with edge info, node_state counts, attachment counts."""
    node_ids = [n["id"] for n in pair["nodes"]]
    ids_str = ", ".join(f"'{nid}'" for nid in node_ids)

    async with sf() as session:
        # Get edges where these nodes are parent or child
        edge_result = await session.execute(
            text(f"""SELECT e.id, e.parent_node_id, e.child_node_id,
                            e.relationship_type, e.weight,
                            pn.name as parent_name, pn.node_type as parent_type,
                            cn.name as child_name, cn.node_type as child_type
                     FROM world_node_edges e
                     JOIN world_nodes pn ON e.parent_node_id = pn.id
                     JOIN world_nodes cn ON e.child_node_id = cn.id
                     WHERE e.parent_node_id IN ({ids_str}) OR e.child_node_id IN ({ids_str})
                     ORDER BY e.relationship_type""")
        )
        edges = [dict(zip(r._mapping.keys(), r._mapping.values())) for r in edge_result.fetchall()]

        # Count node_states per node
        state_result = await session.execute(
            text(f"""SELECT node_id, count(*) as cnt
                     FROM node_states WHERE node_id IN ({ids_str})
                     GROUP BY node_id""")
        )
        state_counts = {str(r[0]): r[1] for r in state_result.fetchall()}

        # Count attachments per node
        att_result = await session.execute(
            text(f"""SELECT node_id, count(*) as cnt
                     FROM node_attachments WHERE node_id IN ({ids_str})
                     GROUP BY node_id""")
        )
        att_counts = {str(r[0]): r[1] for r in att_result.fetchall()}

    # Enrich each node
    for node in pair["nodes"]:
        nid = node["id"]
        node["edge_count"] = sum(1 for e in edges if e["parent_node_id"] == nid or e["child_node_id"] == nid)
        node["edges"] = [
            {"id": e["id"],
             "parent": e["parent_name"], "child": e["child_name"],
             "parent_id": e["parent_node_id"], "child_id": e["child_node_id"],
             "type": e["relationship_type"], "weight": e["weight"]}
            for e in edges
            if e["parent_node_id"] == nid or e["child_node_id"] == nid
        ]
        node["state_count"] = state_counts.get(nid, 0)
        node["attachment_count"] = att_counts.get(nid, 0)

    pair["edges"] = edges
    return pair


# ── MERGE execution ──────────────────────────────────────────────

async def execute_merge(survivor_id: str, victim_id: str, engine, ts=None):
    """Execute a full merge: repoint all FKs, deactivate victim, clean up."""
    if ts is None:
        ts = utcnow()
    elif isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    sf = write_session(engine)
    sid = uuid.UUID(survivor_id)
    vid = uuid.UUID(victim_id)
    report = []

    async with sf() as session:
        # 1. Fetch survivor and victim
        survivor = (await session.execute(
            text("SELECT * FROM world_nodes WHERE id = :id"), {"id": sid}
        )).fetchone()
        victim = (await session.execute(
            text("SELECT * FROM world_nodes WHERE id = :id"), {"id": vid}
        )).fetchone()
        if not survivor or not victim:
            return {"error": "survivor or victim not found"}

        # 2. node_attachments: repoint + dedup
        existing_att = set()
        er = await session.execute(
            text("SELECT attachment_type, attachment_id FROM node_attachments WHERE node_id = :nid"),
            {"nid": sid},
        )
        for row in er.fetchall():
            existing_att.add((row[0], str(row[1])))

        victim_atts = (await session.execute(
            text("SELECT * FROM node_attachments WHERE node_id = :nid"), {"nid": vid}
        )).fetchall()

        repointed_att, deleted_att = 0, 0
        for att in victim_atts:
            key = (att._mapping["attachment_type"], str(att._mapping["attachment_id"]))
            if key in existing_att:
                await session.execute(
                    text("DELETE FROM node_attachments WHERE id = :aid"),
                    {"aid": att._mapping["id"]},
                )
                deleted_att += 1
            else:
                await session.execute(
                    text("UPDATE node_attachments SET node_id = :sid WHERE id = :aid"),
                    {"sid": sid, "aid": att._mapping["id"]},
                )
                existing_att.add(key)
                repointed_att += 1
        report.append(f"node_attachments: {repointed_att} repointed, {deleted_att} dedup deleted")

        # 3. conflict_detections
        r = await session.execute(
            text("UPDATE conflict_detections SET node_id = :sid WHERE node_id = :vid"),
            {"sid": sid, "vid": vid},
        )
        report.append(f"conflict_detections: {r.rowcount} repointed")

        # 4. trading_operations
        r = await session.execute(
            text("UPDATE trading_operations SET target_node_id = :sid WHERE target_node_id = :vid"),
            {"sid": sid, "vid": vid},
        )
        report.append(f"trading_operations: {r.rowcount} repointed")

        # 5. entities
        r = await session.execute(
            text("UPDATE entities SET linked_node_id = :sid WHERE linked_node_id = :vid"),
            {"sid": sid, "vid": vid},
        )
        report.append(f"entities: {r.rowcount} repointed")

        # 6. importance_rankings
        r = await session.execute(
            text("UPDATE importance_rankings SET target_id = :sid WHERE target_id = :vid AND target_type = 'node'"),
            {"sid": sid, "vid": vid},
        )
        report.append(f"importance_rankings: {r.rowcount} repointed")

        # 7. node_states: close victim only. Survivor keeps its existing state.
        await session.execute(
            text("UPDATE node_states SET effective_to = :ts WHERE node_id = :vid AND effective_to IS NULL"),
            {"ts": ts, "vid": vid},
        )
        report.append("node_states: victim closed, survivor state preserved")

        # 8. world_node_edges: repoint + dedup
        existing_edges = set()
        er = await session.execute(
            text("""SELECT parent_node_id, child_node_id, relationship_type
                    FROM world_node_edges
                    WHERE parent_node_id = :sid OR child_node_id = :sid"""),
            {"sid": sid},
        )
        for row in er.fetchall():
            existing_edges.add((str(row[0]), str(row[1]), row[2]))

        edge_repointed, edge_deleted = 0, 0
        # Victim as parent
        victim_parent_edges = (await session.execute(
            text("SELECT * FROM world_node_edges WHERE parent_node_id = :vid"), {"vid": vid}
        )).fetchall()
        for edge in victim_parent_edges:
            eid = edge._mapping["id"]
            key = (survivor_id, str(edge._mapping["child_node_id"]), edge._mapping["relationship_type"])
            if key in existing_edges:
                await session.execute(text("DELETE FROM world_node_edges WHERE id = :eid"), {"eid": eid})
                edge_deleted += 1
            else:
                await session.execute(
                    text("UPDATE world_node_edges SET parent_node_id = :sid WHERE id = :eid"),
                    {"sid": sid, "eid": eid},
                )
                existing_edges.add(key)
                edge_repointed += 1

        # Victim as child
        victim_child_edges = (await session.execute(
            text("SELECT * FROM world_node_edges WHERE child_node_id = :vid"), {"vid": vid}
        )).fetchall()
        for edge in victim_child_edges:
            eid = edge._mapping["id"]
            key = (str(edge._mapping["parent_node_id"]), survivor_id, edge._mapping["relationship_type"])
            if key in existing_edges:
                await session.execute(text("DELETE FROM world_node_edges WHERE id = :eid"), {"eid": eid})
                edge_deleted += 1
            else:
                await session.execute(
                    text("UPDATE world_node_edges SET child_node_id = :sid WHERE id = :eid"),
                    {"sid": sid, "eid": eid},
                )
                existing_edges.add(key)
                edge_repointed += 1
        report.append(f"world_node_edges: {edge_repointed} repointed, {edge_deleted} dedup deleted")

        # 9. Merge aliases from both nodes into survivor
        s_aliases = [a for a in (survivor._mapping["aliases"] or []) if a]
        for name in (victim._mapping["aliases"] or []) + [victim._mapping["name"]]:
            if name and name not in s_aliases:
                s_aliases.append(name)

        s_desc = survivor._mapping["description"] or ""
        v_desc = victim._mapping["description"] or ""
        merged_desc = v_desc if len(v_desc) > len(s_desc) else s_desc

        await session.execute(
            text("""UPDATE world_nodes
                    SET aliases = :a, description = :d
                    WHERE id = :nid"""),
            {"a": s_aliases, "d": merged_desc, "nid": sid},
        )

        # 10. Deactivate victim
        await session.execute(
            text("UPDATE world_nodes SET is_active = false WHERE id = :vid"),
            {"vid": vid},
        )

        report.append(f"survivor updated: aliases merged, description kept (len={len(merged_desc)})")
        report.append(f"victim [{victim._mapping['name']}]: is_active=false")

        # 11. Remove self-referencing edges on survivor (created when victim had
        # edges to/from survivor and we repointed them)
        self_edges = await session.execute(
            text("DELETE FROM world_node_edges WHERE parent_node_id = :sid AND child_node_id = :sid"),
            {"sid": sid},
        )
        if self_edges.rowcount:
            report.append(f"ghost cleanup: {self_edges.rowcount} self-referencing edges on survivor deleted")

        await session.commit()

    return {
        "action": "merge",
        "survivor_id": survivor_id,
        "victim_id": victim_id,
        "survivor_name": survivor._mapping["name"],
        "victim_name": victim._mapping["name"],
        "report": report,
    }


# ── RENAME execution ────────────────────────────────────────────

async def execute_rename(node_id: str, new_name: str, engine, ts=None):
    """Rename a node (same UUID, no FK changes needed)."""
    if ts is None:
        ts = utcnow()
    elif isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    sf = write_session(engine)

    async with sf() as session:
        # Check for collision
        existing = await session.execute(
            text("SELECT id, name, node_type FROM world_nodes WHERE name = :name AND id != :nid"),
            {"name": new_name, "nid": uuid.UUID(node_id)},
        )
        collided = existing.fetchone()

        if collided:
            return {
                "action": "rename",
                "node_id": node_id,
                "new_name": new_name,
                "status": "collision",
                "collision_node_id": str(collided._mapping["id"]),
                "collision_node_name": collided._mapping["name"],
                "collision_node_type": collided._mapping["node_type"],
                "error": f"Rename collision: '{new_name}' already exists (type={collided._mapping['node_type']})",
            }

        old = (await session.execute(
            text("SELECT name FROM world_nodes WHERE id = :id"), {"id": uuid.UUID(node_id)}
        )).fetchone()

        await session.execute(
            text("UPDATE world_nodes SET name = :name WHERE id = :nid"),
            {"name": new_name, "nid": uuid.UUID(node_id)},
        )
        await session.commit()

        return {
            "action": "rename",
            "node_id": node_id,
            "old_name": old._mapping["name"] if old else "?",
            "new_name": new_name,
            "status": "done",
        }


# ── KEEP_BOTH: create edge between two nodes ─────────────────────

async def execute_keep_both(parent_id: str, child_id: str, edge_type: str, weight: float, engine, ts=None):
    """Keep both nodes but connect them with an edge."""
    if ts is None:
        ts = utcnow()
    elif isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    sf = write_session(engine)

    async with sf() as session:
        eid = uuid.uuid4()
        try:
            await session.execute(
                text("""INSERT INTO world_node_edges
                        (id, parent_node_id, child_node_id, relationship_type, weight, created_at, updated_at)
                        VALUES (:id, :pid, :cid, :rtype, :w, :ts, :ts)
                        ON CONFLICT (parent_node_id, child_node_id, relationship_type) DO NOTHING"""),
                {"id": eid, "pid": uuid.UUID(parent_id), "cid": uuid.UUID(child_id),
                 "rtype": edge_type, "w": weight, "ts": ts},
            )
            await session.commit()
        except Exception as e:
            return {"action": "keep_both", "status": "failed", "error": str(e)}

        return {
            "action": "keep_both",
            "parent_id": parent_id,
            "child_id": child_id,
            "edge_type": edge_type,
            "weight": weight,
            "edge_id": str(eid),
            "status": "done",
        }


# ── Main ─────────────────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python nodes/phase1_resolve.py <pair_json>", file=sys.stderr)
        print("       uv run python nodes/phase1_resolve.py --file <pairs.json>", file=sys.stderr)
        sys.exit(1)

    engine = get_engine()

    if sys.argv[1] == "--file":
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            pairs = json.load(f)
        print(f"Loaded {len(pairs)} pairs from {sys.argv[2]}", file=sys.stderr)
        for pair in pairs:
            # Print enriched context for each pair (intended for agent consumption)
            sf = read_session(engine)
            enriched = await load_pair_context(sf, pair)
            enriched["pair_id"] = pair.get("pair_id", f"dup_{pairs.index(pair)+1:04d}")
            print(json.dumps(enriched, ensure_ascii=False, default=str))
    else:
        # Single pair from command line
        pair = json.loads(sys.argv[1])
        sf = read_session(engine)
        enriched = await load_pair_context(sf, pair)
        enriched["pair_id"] = pair.get("pair_id", "dup_0001")
        print(json.dumps(enriched, ensure_ascii=False, default=str))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
