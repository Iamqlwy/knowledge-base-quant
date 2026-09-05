"""
Phase 2.4: Resolve edge candidates — create or skip.

Takes candidate edge JSON Lines (from phase2_scan.py) and creates edges.
Designed to be called per-candidate by an orchestrator/agent.

Usage:
    uv run python nodes/phase2_resolve.py <candidate_json_line>
    uv run python nodes/phase2_resolve.py --file <candidates.json>
    uv run python nodes/phase2_resolve.py --create <parent_id> <child_id> <edge_type> [weight] [ts]
"""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from sqlalchemy import text
from nodes.common import get_engine, write_session, utcnow


async def create_edge(parent_id: str, child_id: str, edge_type: str,
                      weight: float = 0.5, engine=None, ts=None) -> dict:
    """Create a world_node_edge. Returns result dict."""
    if ts is None:
        ts = utcnow()
    elif isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    sf = write_session(engine)
    pid = uuid.UUID(parent_id)
    cid = uuid.UUID(child_id)

    async with sf() as session:
        # Verify both nodes exist and are active
        parent = (await session.execute(
            text("SELECT id, name, node_type FROM world_nodes WHERE id = :id AND is_active = true"),
            {"id": pid},
        )).fetchone()
        child = (await session.execute(
            text("SELECT id, name, node_type FROM world_nodes WHERE id = :id AND is_active = true"),
            {"id": cid},
        )).fetchone()

        if not parent:
            return {"status": "failed", "error": f"parent node not found or inactive: {parent_id}"}
        if not child:
            return {"status": "failed", "error": f"child node not found or inactive: {child_id}"}
        if parent_id == child_id:
            return {"status": "failed", "error": "self-referencing edge not allowed"}

        # Check existing
        existing = await session.execute(
            text("""SELECT id FROM world_node_edges
                    WHERE parent_node_id = :pid AND child_node_id = :cid
                      AND relationship_type = :rtype"""),
            {"pid": pid, "cid": cid, "rtype": edge_type},
        )
        if existing.fetchone():
            return {"status": "skipped", "reason": "edge already exists"}

        eid = uuid.uuid4()
        await session.execute(
            text("""INSERT INTO world_node_edges
                    (id, parent_node_id, child_node_id, relationship_type, weight, created_at, updated_at)
                    VALUES (:id, :pid, :cid, :rtype, :w, :ts, :ts)"""),
            {"id": eid, "pid": pid, "cid": cid, "rtype": edge_type, "w": weight, "ts": ts},
        )
        await session.commit()

    return {
        "status": "created",
        "edge_id": str(eid),
        "parent_id": parent_id,
        "parent_name": parent._mapping["name"],
        "child_id": child_id,
        "child_name": child._mapping["name"],
        "edge_type": edge_type,
        "weight": weight,
    }


async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python nodes/phase2_resolve.py <candidate_json>", file=sys.stderr)
        print("       uv run python nodes/phase2_resolve.py --file <candidates.json>", file=sys.stderr)
        print("       uv run python nodes/phase2_resolve.py --create <parent_id> <child_id> <edge_type> [weight]", file=sys.stderr)
        sys.exit(1)

    engine = get_engine()

    if sys.argv[1] == "--create":
        # Direct edge creation
        if len(sys.argv) < 5:
            print("Usage: uv run python nodes/phase2_resolve.py --create <parent_id> <child_id> <edge_type> [weight] [ts]",
                  file=sys.stderr)
            sys.exit(1)
        parent_id = sys.argv[2]
        child_id = sys.argv[3]
        edge_type = sys.argv[4]
        weight = float(sys.argv[5]) if len(sys.argv) > 5 else 0.5
        ts = sys.argv[6] if len(sys.argv) > 6 else None
        result = await create_edge(parent_id, child_id, edge_type, weight, engine, ts=ts)
        print(json.dumps(result, ensure_ascii=False))

    elif sys.argv[1] == "--file":
        # Process all candidates from file
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            candidates = json.load(f)

        print(f"Processing {sum(len(c['candidates']) for c in candidates)} candidates...", file=sys.stderr)

        created, skipped, failed = 0, 0, 0
        for group in candidates:
            for cand in group["candidates"]:
                proposed = cand["proposed"]
                result = await create_edge(
                    proposed["parent_id"],
                    proposed["child_id"],
                    proposed["edge_type"],
                    cand.get("weight", 0.5),
                    engine,
                )
                if result["status"] == "created":
                    created += 1
                    print(f"  ✓ {result['parent_name']} --({result['edge_type']})--> {result['child_name']}")
                elif result["status"] == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    print(f"  ✗ {result.get('error', 'unknown')}", file=sys.stderr)

        print(f"\n结果: {created} created, {skipped} skipped, {failed} failed", file=sys.stderr)

    else:
        # Single candidate from command line
        candidate = json.loads(sys.argv[1])
        proposed = candidate.get("proposed", {})
        result = await create_edge(
            proposed.get("parent_id"),
            proposed.get("child_id"),
            proposed.get("edge_type", "belongs_to"),
            proposed.get("weight", 0.5),
            engine,
        )
        print(json.dumps(result, ensure_ascii=False))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
