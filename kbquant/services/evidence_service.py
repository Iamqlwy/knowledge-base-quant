import uuid

from sqlalchemy import select
from sqlalchemy.orm import defer

from kbquant.database import LazyDB
from kbquant.models.raw_information import RawInformation
from kbquant.models.analysis import Analysis
from kbquant.models.node_state import NodeState
from kbquant.models.node_attachment import NodeAttachment


class EvidenceService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def trace(self, target_type: str, target_id: uuid.UUID, depth: int = 3) -> dict:
        chain = []
        visited = set()

        async with self.db.session() as session:
            if target_type == "raw_info":
                info = await session.execute(
                    select(RawInformation)
                    .where(RawInformation.id == target_id)
                    .options(defer(RawInformation.embedding))
                )
                if row := info.scalar_one_or_none():
                    chain.append({
                        "level": 0,
                        "items": [{"type": "raw_info", "id": row.id, "title": row.title,
                                   "summary": row.body[:200], "timestamp": row.published_at.isoformat()}],
                    })
                    analyses = await session.execute(
                        select(Analysis)
                        .where(Analysis.root_raw_info_ids.contains([row.id]))
                        .options(defer(Analysis.embedding))
                    )
                    if a_rows := analyses.scalars().all():
                        chain.append({
                            "level": 1,
                            "items": [{"type": "analysis", "id": a.id, "title": a.title,
                                       "summary": a.content[:200], "timestamp": a.created_at.isoformat()} for a in a_rows],
                        })

            elif target_type == "node_state":
                state = await session.execute(
                    select(NodeState)
                    .where(NodeState.id == target_id)
                    .options(defer(NodeState.embedding))
                )
                if row := state.scalar_one_or_none():
                    node_state_title = row.state_summary or row.core_logic or "node_state"
                    chain.append({
                        "level": 0,
                        "items": [{"type": "node_state", "id": row.id,
                                   "title": node_state_title,
                                   "summary": row.state_summary or row.core_logic or "",
                                   "timestamp": row.effective_from.isoformat()}],
                    })
                    if row.key_evidence_ids:
                        evidence_ids = []
                        for ev_id in row.key_evidence_ids[:10]:
                            if ev_id not in visited:
                                visited.add(ev_id)
                                evidence_ids.append(ev_id)
                        if evidence_ids:
                            info_result = await session.execute(
                                select(RawInformation)
                                .where(RawInformation.id.in_(evidence_ids))
                                .options(defer(RawInformation.embedding))
                            )
                            info_map = {info.id: info for info in info_result.scalars().all()}
                            for ev_id in evidence_ids:
                                if i_row := info_map.get(ev_id):
                                    chain.append({
                                        "level": 1,
                                        "items": [{"type": "raw_info", "id": i_row.id, "title": i_row.title,
                                                   "summary": i_row.body[:200], "timestamp": i_row.published_at.isoformat()}],
                                    })

        return {"root": {"type": target_type, "id": str(target_id)}, "evidence_chain": chain}

    async def trace_node(self, node_id: uuid.UUID, aspect: str | None = None) -> dict:
        async with self.db.session() as session:
            attachments = await session.execute(
                select(NodeAttachment).where(NodeAttachment.node_id == node_id)
            )
            attach_list = attachments.scalars().all()
            evidence_items = []
            raw_info_ids = [
                a.attachment_id for a in attach_list[:50]
                if a.attachment_type == "raw_info"
            ]
            if raw_info_ids:
                info_result = await session.execute(
                    select(RawInformation)
                    .where(RawInformation.id.in_(raw_info_ids))
                    .options(defer(RawInformation.embedding))
                )
                info_map = {info.id: info for info in info_result.scalars().all()}
                for raw_info_id in raw_info_ids:
                    if row := info_map.get(raw_info_id):
                        evidence_items.append({
                            "id": row.id, "title": row.title, "type": "raw_info",
                            "source": row.source, "published_at": row.published_at.isoformat(),
                        })
        return {"aspect": aspect or "all", "evidence_items": evidence_items}
