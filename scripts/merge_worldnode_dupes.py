"""
Idempotent merge of near-duplicate world_nodes.

Operations (skip if already done):
  Merges (victim → survivor, all FK references repointed):
    光伏行业 → 光伏
    脑机接口板块 → 脑机接口
    脑机接口概念 → 脑机接口
    数据要素板块 → 数据要素
    西藏基建板块 → 基建板块
    海上风电板块 → 风电板块
    广州（低空经济） → 低空经济

  Renames (same node, just update name):
    太空光伏概念 → 太空光伏
    深圳AI服务器产业链行动计划 → AI服务器

After merging: update surviving node's updated_at to 2026-04-07,
and create a NodeState entry documenting the merge.
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update, text
from kbquant.database import write_async_session
from kbquant.models.world_node import WorldNode, WorldNodeEdge
from kbquant.models.node_state import NodeState
from kbquant.models.node_attachment import NodeAttachment
from kbquant.models.conflict_detection import ConflictDetection
from kbquant.models.trading_operation import TradingOperation
from kbquant.models.importance_ranking import ImportanceRanking
from kbquant.models.entity import Entity

MERGE_DATE = datetime(2026, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
NOW = datetime.now(timezone.utc)

# (survivor_name, victim_name)
MERGES: list[tuple[str, str]] = [
    ("光伏", "光伏行业"),
    ("脑机接口", "脑机接口板块"),
    ("脑机接口", "脑机接口概念"),
    ("数据要素", "数据要素板块"),
    ("基建板块", "西藏基建板块"),
    ("风电板块", "海上风电板块"),
    ("低空经济", "广州（低空经济）"),
]

# (old_name, new_name)
RENAMES: list[tuple[str, str]] = [
    ("太空光伏概念", "太空光伏"),
    ("深圳AI服务器产业链行动计划", "AI服务器"),
]


def _build_merge_note(survivor_name: str, victim_name: str) -> str:
    return (
        f"合并节点：'{victim_name}' 合并入 '{survivor_name}'。"
        f"所有关联数据已重新指向本节点。"
        f"执行时间：{NOW.isoformat(timespec='seconds')}"
    )


async def main():
    async with write_async_session() as session:
        # 1. Load all potentially relevant nodes by ANY name they might have
        all_names = set()
        for s, v in MERGES:
            all_names.add(s)
            all_names.add(v)
        for old, new in RENAMES:
            all_names.add(old)
            all_names.add(new)

        result = await session.execute(
            select(WorldNode).where(WorldNode.name.in_(list(all_names)))
        )
        nodes_by_name: dict[str, WorldNode] = {}
        for n in result.scalars().all():
            nodes_by_name[n.name] = n

        # --- Execute renames (idempotent) ---
        for old_name, new_name in RENAMES:
            if old_name not in nodes_by_name and new_name not in nodes_by_name:
                print(f"SKIP RENAME [{old_name}] → [{new_name}]: neither name found (already done?)")
                continue
            if old_name not in nodes_by_name:
                # Already renamed in previous run
                node = nodes_by_name.get(new_name)
                if node:
                    print(f"SKIP RENAME [{old_name}] → [{new_name}]: old name not found, new name exists (already done)")
                else:
                    print(f"WARNING RENAME [{old_name}] → [{new_name}]: old name not found, new name also not found")
                continue

            node = nodes_by_name[old_name]
            if new_name in nodes_by_name and nodes_by_name[new_name].id != node.id:
                other = nodes_by_name[new_name]
                if other.node_type == node.node_type:
                    print(f"ERROR rename [{old_name}] → [{new_name}]: collision with existing node {other.id} (same type={other.node_type})")
                    return
                else:
                    # Different type, rename is safe
                    pass

            node.name = new_name
            node.updated_at = MERGE_DATE
            session.add(node)
            # Update lookup so subsequent merges can find by new name
            nodes_by_name[new_name] = node
            nodes_by_name.pop(old_name, None)
            print(f"RENAME: [{old_name}] → [{new_name}] (id={node.id}, type={node.node_type})")

        await session.flush()

        # --- Execute merges (idempotent) ---
        for survivor_name, victim_name in MERGES:
            survivor = nodes_by_name.get(survivor_name)
            victim = nodes_by_name.get(victim_name)

            if not survivor:
                print(f"SKIP MERGE [{victim_name}] → [{survivor_name}]: survivor not found")
                continue
            if not victim:
                print(f"SKIP MERGE [{victim_name}] → [{survivor_name}]: victim not found (already merged?)")
                continue
            if not victim.is_active:
                print(f"SKIP MERGE [{victim_name}] → [{survivor_name}]: victim already inactive (already merged)")
                continue

            sid = survivor.id
            vid = victim.id

            print(f"\nMERGE: [{victim_name}] (id={vid}, type={victim.node_type}) → [{survivor_name}] (id={sid}, type={survivor.node_type})")

            repointed = 0
            deleted_dupes = 0

            # --- node_attachments ---
            existing_att = set()
            er = await session.execute(
                select(NodeAttachment.attachment_type, NodeAttachment.attachment_id)
                .where(NodeAttachment.node_id == sid)
            )
            for row in er.all():
                existing_att.add((row[0], row[1]))

            victim_att = (await session.execute(
                select(NodeAttachment).where(NodeAttachment.node_id == vid)
            )).scalars().all()

            for att in victim_att:
                key = (att.attachment_type, att.attachment_id)
                if key in existing_att:
                    await session.delete(att)
                    deleted_dupes += 1
                else:
                    att.node_id = sid
                    session.add(att)
                    repointed += 1
            print(f"  node_attachments: {repointed} repointed, {deleted_dupes} dupes deleted")

            # --- conflict_detections ---
            r = await session.execute(
                update(ConflictDetection).where(ConflictDetection.node_id == vid).values(node_id=sid)
            )
            print(f"  conflict_detections: {r.rowcount} repointed")

            # --- trading_operations ---
            r = await session.execute(
                update(TradingOperation).where(TradingOperation.target_node_id == vid).values(target_node_id=sid)
            )
            print(f"  trading_operations: {r.rowcount} repointed")

            # --- importance_rankings (target_type='node') ---
            r = await session.execute(
                update(ImportanceRanking)
                .where(ImportanceRanking.target_type == "node", ImportanceRanking.target_id == vid)
                .values(target_id=sid)
            )
            print(f"  importance_rankings: {r.rowcount} repointed")

            # --- entities.linked_node_id ---
            r = await session.execute(
                update(Entity).where(Entity.linked_node_id == vid).values(linked_node_id=sid)
            )
            print(f"  entities: {r.rowcount} repointed")

            # --- node_states ---
            # Close current victim states (can't repoint due to version uniqueness constraint)
            r = await session.execute(
                update(NodeState)
                .where(NodeState.node_id == vid, NodeState.effective_to.is_(None))
                .values(effective_to=NOW)
            )
            victim_closed = r.rowcount
            print(f"  node_states (victim): {victim_closed} closed")

            # --- world_node_edges ---
            existing_edges = set()
            er = await session.execute(
                select(WorldNodeEdge.parent_node_id, WorldNodeEdge.child_node_id, WorldNodeEdge.relationship_type)
                .where(
                    (WorldNodeEdge.parent_node_id == sid) | (WorldNodeEdge.child_node_id == sid)
                )
            )
            for row in er.all():
                existing_edges.add((row[0], row[1], row[2]))

            victim_parent_edges = (await session.execute(
                select(WorldNodeEdge).where(WorldNodeEdge.parent_node_id == vid)
            )).scalars().all()

            for edge in victim_parent_edges:
                key = (sid, edge.child_node_id, edge.relationship_type)
                if key in existing_edges:
                    await session.delete(edge)
                    deleted_dupes += 1
                else:
                    edge.parent_node_id = sid
                    session.add(edge)
                    existing_edges.add(key)
                    repointed += 1

            victim_child_edges = (await session.execute(
                select(WorldNodeEdge).where(WorldNodeEdge.child_node_id == vid)
            )).scalars().all()

            for edge in victim_child_edges:
                key = (edge.parent_node_id, sid, edge.relationship_type)
                if key in existing_edges:
                    await session.delete(edge)
                    deleted_dupes += 1
                else:
                    edge.child_node_id = sid
                    session.add(edge)
                    existing_edges.add(key)
                    repointed += 1

            print(f"  world_node_edges: {repointed} repointed, {deleted_dupes} dupes deleted")

            # --- Create NodeState for survivor documenting the merge ---
            r = await session.execute(
                update(NodeState)
                .where(NodeState.node_id == sid, NodeState.effective_to.is_(None))
                .values(effective_to=MERGE_DATE)
            )
            print(f"  node_states (survivor current): {r.rowcount} closed at {MERGE_DATE.isoformat()}")

            max_ver = await session.execute(
                select(text("COALESCE(MAX(version), 0)"))
                .select_from(NodeState)
                .where(NodeState.node_id == sid)
            )
            next_version = max_ver.scalar() + 1

            merge_note = _build_merge_note(survivor_name, victim_name)
            new_state = NodeState(
                id=uuid4(),
                node_id=sid,
                version=next_version,
                effective_from=MERGE_DATE,
                effective_to=None,
                core_logic=f"节点合并：'{victim_name}' → '{survivor_name}'",
                state_summary=merge_note,
                recent_changes=merge_note,
                created_at=MERGE_DATE,
                updated_at=MERGE_DATE,
            )
            session.add(new_state)
            print(f"  node_state: created version={next_version} for survivor")

            # Deactivate victim
            victim.is_active = False
            victim.updated_at = MERGE_DATE
            session.add(victim)
            print(f"  victim [{victim_name}]: is_active=False")

            # Set survivor updated_at
            survivor.updated_at = MERGE_DATE
            session.add(survivor)
            print(f"  survivor [{survivor_name}]: updated_at={MERGE_DATE.isoformat()}")

        print("\n" + "=" * 60)
        print("Committing...")
        await session.commit()
        print("Done. All changes committed.")


if __name__ == "__main__":
    asyncio.run(main())
