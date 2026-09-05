"""
Merge near-duplicate world_nodes identified by phase1_scan.py.
Survivor = simpler/more general name; victim = variant with suffix/extra detail.

After merging: update surviving node's updated_at to 2026-05-06 00:00 CST,
deactivate victims, and create NodeState entries documenting the merge.
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

MERGE_DATE = datetime(2026, 5, 5, 16, 0, 0, tzinfo=timezone.utc)  # 2026-05-06 00:00 CST
NOW = datetime.now(timezone.utc)

# (survivor=更通用, victim=变体/带后缀)
MERGES: list[tuple[str, str]] = [
    ("大普微", "大普微电子"),                # dup_0016: 简称 vs 全称
    ("森麒麟", "森麒麟（002984）"),           # dup_0018: 无代码 vs 带代码
    ("水井坊", "水井坊（600779.SH）"),        # dup_0019
    ("科沃斯", "科沃斯（603486）"),           # dup_0020
    ("紫江企业", "紫江企业（2026年4月）"),     # dup_0021: 无日期 vs 带日期
    ("聚石化学", "聚石化学（2026年4月）"),     # dup_0022
    ("航天南湖", "航天南湖（688552）"),        # dup_0023
    ("美伊停火谈判", "美伊停火谈判进展"),      # dup_0029: 短名 vs 加"进展"
    ("具身智能产业链", "具身智能板块"),        # dup_0030: 产业链更全面
    ("半导体设备板块", "半导体测试设备"),      # dup_0031: 父类 vs 子类
    ("AI应用板块", "港股AI应用板块"),          # dup_0032: 合并后通用名
    ("AI应用板块", "A股AI应用板块"),           # dup_0032: 合并后通用名
    ("安诺其", "安诺其重大资产重组事件（2026年4月）"),  # dup_0028: 事件节点合并入公司节点
]


def _build_merge_note(survivor_name: str, victim_name: str) -> str:
    return (
        f"合并节点：'{victim_name}' 合并入 '{survivor_name}'。"
        f"所有关联数据已重新指向本节点。"
        f"执行时间：{NOW.isoformat(timespec='seconds')}"
    )


async def main():
    async with write_async_session() as session:
        all_names = set()
        for s, v in MERGES:
            all_names.add(s)
            all_names.add(v)

        result = await session.execute(
            select(WorldNode).where(WorldNode.name.in_(list(all_names)))
        )
        nodes_by_name: dict[str, WorldNode] = {}
        for n in result.scalars().all():
            nodes_by_name[n.name] = n

        for name in all_names:
            if name not in nodes_by_name:
                print(f"WARNING: name [{name}] not found in DB")

        # If both A股AI应用板块 and 港股AI应用板块 exist, AI应用板块 might not exist yet.
        # Rename A股AI应用板块 → AI应用板块 first if AI应用板块 doesn't exist.
        a_stock = nodes_by_name.get("A股AI应用板块")
        ai_app = nodes_by_name.get("AI应用板块")
        if a_stock and not ai_app:
            print(f"\nRENAME: [A股AI应用板块] → [AI应用板块] (id={a_stock.id})")
            a_stock.name = "AI应用板块"
            a_stock.updated_at = MERGE_DATE
            session.add(a_stock)
            nodes_by_name["AI应用板块"] = a_stock
            nodes_by_name.pop("A股AI应用板块", None)
        await session.flush()

        # --- Execute merges ---
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
                print(f"SKIP MERGE [{victim_name}] → [{survivor_name}]: victim already inactive")
                continue
            if survivor.id == victim.id:
                print(f"SKIP MERGE [{victim_name}] → [{survivor_name}]: same node")
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

            # --- importance_rankings ---
            r = await session.execute(
                update(ImportanceRanking)
                .where(ImportanceRanking.target_type == "node", ImportanceRanking.target_id == vid)
                .values(target_id=sid)
            )
            print(f"  importance_rankings: {r.rowcount} repointed")

            # --- entities ---
            r = await session.execute(
                update(Entity).where(Entity.linked_node_id == vid).values(linked_node_id=sid)
            )
            print(f"  entities: {r.rowcount} repointed")

            # --- node_states (victim) ---
            r = await session.execute(
                update(NodeState)
                .where(NodeState.node_id == vid, NodeState.effective_to.is_(None))
                .values(effective_to=NOW)
            )
            print(f"  node_states (victim): {r.rowcount} closed")

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

            # --- NodeState for survivor ---
            r = await session.execute(
                update(NodeState)
                .where(NodeState.node_id == sid, NodeState.effective_to.is_(None))
                .values(effective_to=MERGE_DATE)
            )
            print(f"  node_states (survivor current): {r.rowcount} closed")

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

            # Survivor updated_at
            survivor.updated_at = MERGE_DATE
            session.add(survivor)
            print(f"  survivor [{survivor_name}]: updated_at={MERGE_DATE.isoformat()}")

        print("\n" + "=" * 60)
        print("Committing...")
        await session.commit()
        print("Done. All changes committed.")


if __name__ == "__main__":
    asyncio.run(main())
