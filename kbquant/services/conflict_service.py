import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func

from kbquant.database import LazyDB
from kbquant.models.conflict_detection import ConflictDetection


class ConflictService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def detect(self, *, node_id: uuid.UUID, existing_claim: str, conflicting_claim: str,
                     conflict_type: str = "contradiction",
                     existing_evidence_id: uuid.UUID | None = None,
                     conflicting_evidence_id: uuid.UUID | None = None) -> ConflictDetection:
        async with self.db.session() as session:
            conflict = ConflictDetection(
                node_id=node_id, existing_claim=existing_claim,
                conflicting_claim=conflicting_claim, conflict_type=conflict_type,
                existing_evidence_id=existing_evidence_id,
                conflicting_evidence_id=conflicting_evidence_id,
            )
            session.add(conflict)
            await session.flush()
            return conflict

    async def list_items(self, *, node_id: uuid.UUID | None = None, conflict_type: str | None = None,
                         is_resolved: bool | None = None,
                         page: int = 1, page_size: int = 20) -> tuple[list[ConflictDetection], int]:
        async with self.db.session() as session:
            query = select(ConflictDetection)
            count_query = select(func.count()).select_from(ConflictDetection)
            if node_id:
                query = query.where(ConflictDetection.node_id == node_id)
                count_query = count_query.where(ConflictDetection.node_id == node_id)
            if conflict_type:
                query = query.where(ConflictDetection.conflict_type == conflict_type)
                count_query = count_query.where(ConflictDetection.conflict_type == conflict_type)
            if is_resolved is True:
                query = query.where(ConflictDetection.resolved_at != None)
                count_query = count_query.where(ConflictDetection.resolved_at != None)
            elif is_resolved is False:
                query = query.where(ConflictDetection.resolved_at == None)
                count_query = count_query.where(ConflictDetection.resolved_at == None)
            query = query.order_by(ConflictDetection.created_at.desc())
            query = query.offset((page - 1) * page_size).limit(page_size)
            total_result = await session.execute(count_query)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())
            return items, total

    async def resolve(self, conflict_id: uuid.UUID, resolution: str) -> ConflictDetection | None:
        async with self.db.session() as session:
            result = await session.execute(select(ConflictDetection).where(ConflictDetection.id == conflict_id))
            conflict = result.scalar_one_or_none()
            if not conflict:
                return None
            conflict.resolution = resolution
            conflict.resolved_at = datetime.now(timezone.utc)
            session.add(conflict)
            await session.flush()
            return conflict
