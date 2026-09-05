import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from kbquant.database import LazyDB
from kbquant.models.processing_queue import ProcessingQueue


class PipelineService:
    VALID_STATUSES = [
        "ingested", "deduped", "entities_extracted", "attached_to_nodes",
        "analyzed", "world_model_updated", "trade_validated", "preprocessed", "error",
    ]

    def __init__(self, db: LazyDB):
        self.db = db

    async def _get_queue_entry(self, raw_info_id: uuid.UUID) -> ProcessingQueue | None:
        async with self.db.session() as session:
            return await self._get_queue_entry_scoped(session, raw_info_id)

    @staticmethod
    async def _get_queue_entry_scoped(session, raw_info_id: uuid.UUID) -> ProcessingQueue | None:
        result = await session.execute(
            select(ProcessingQueue).where(ProcessingQueue.raw_info_id == raw_info_id)
        )
        return result.scalar_one_or_none()

    async def _create_queue_entry(self, raw_info_id: uuid.UUID,
                                  preprocess_status: str = "ingested") -> ProcessingQueue:
        async with self.db.session() as session:
            entry = ProcessingQueue(raw_info_id=raw_info_id, preprocess_status=preprocess_status)
            session.add(entry)
            await session.flush()
            return entry

    async def get_or_create_queue_entry(self, raw_info_id: uuid.UUID) -> ProcessingQueue:
        async with self.db.session() as session:
            entry = await self._get_queue_entry_scoped(session, raw_info_id)
            if entry:
                return entry
            entry = ProcessingQueue(raw_info_id=raw_info_id, preprocess_status="ingested")
            session.add(entry)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # A concurrent request created the entry; re-query it.
                entry = await self._get_queue_entry_scoped(session, raw_info_id)
                if entry is None:
                    raise
            return entry

    async def update_status(self, raw_info_id: uuid.UUID, status: str,
                            detail: str | None = None, priority: int | None = None) -> ProcessingQueue:
        async with self.db.session() as session:
            # Lock the row before reading to prevent lost updates on status_history
            entry = await session.execute(
                select(ProcessingQueue)
                .where(ProcessingQueue.raw_info_id == raw_info_id)
                .with_for_update()
            )
            entry = entry.scalar_one_or_none()
            if entry is None:
                entry = ProcessingQueue(raw_info_id=raw_info_id, preprocess_status="ingested")
                session.add(entry)
                try:
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    entry = await session.execute(
                        select(ProcessingQueue)
                        .where(ProcessingQueue.raw_info_id == raw_info_id)
                        .with_for_update()
                    )
                    entry = entry.scalar_one_or_none()
                    if entry is None:
                        raise
            now = datetime.now(timezone.utc)
            history = entry.status_history or []
            history.append({"status": status, "timestamp": now.isoformat(), "detail": detail})
            entry.status = status
            entry.status_history = history
            if priority is not None:
                entry.priority = priority
            if status == "preprocessed":
                entry.completed_at = now
            if status == "error" and detail:
                entry.last_error = detail
            session.add(entry)
            await session.flush()
            return entry

    async def update_preprocess_status(self, raw_info_id: uuid.UUID, preprocess_status: str,
                                       detail: str | None = None) -> ProcessingQueue:
        values: dict[str, str] = {"preprocess_status": preprocess_status}
        if preprocess_status == "error" and detail:
            values["last_error"] = detail

        async with self.db.session() as session:
            updated = await session.execute(
                update(ProcessingQueue)
                .where(ProcessingQueue.raw_info_id == raw_info_id)
                .values(**values)
                .returning(ProcessingQueue.id)
            )
            entry_id = updated.scalar_one_or_none()
            if entry_id is not None:
                return await session.get(ProcessingQueue, entry_id)

            entry = ProcessingQueue(raw_info_id=raw_info_id, preprocess_status=preprocess_status)
            if preprocess_status == "error" and detail:
                entry.last_error = detail
            session.add(entry)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                entry = await self._get_queue_entry_scoped(session, raw_info_id)
                if entry is None:
                    raise
            return entry

    async def list_queue(self, *, status: str | list[str] | None = None, priority_min: int | None = None,
                         agent_assigned: str | None = None,
                         page: int = 1, page_size: int = 20) -> tuple[list, int]:
        async with self.db.session() as session:
            query = select(ProcessingQueue)
            count_query = select(func.count()).select_from(ProcessingQueue)
            if isinstance(status, (list, tuple, set)):
                statuses = list(status)
                if not statuses:
                    return [], 0
                query = query.where(ProcessingQueue.status.in_(statuses))
                count_query = count_query.where(ProcessingQueue.status.in_(statuses))
            elif status:
                query = query.where(ProcessingQueue.status == status)
                count_query = count_query.where(ProcessingQueue.status == status)
            if priority_min is not None:
                query = query.where(ProcessingQueue.priority >= priority_min)
                count_query = count_query.where(ProcessingQueue.priority >= priority_min)
            if agent_assigned:
                query = query.where(ProcessingQueue.agent_assigned == agent_assigned)
                count_query = count_query.where(ProcessingQueue.agent_assigned == agent_assigned)
            query = query.order_by(ProcessingQueue.priority.desc()).offset((page - 1) * page_size).limit(page_size)
            total_result = await session.execute(count_query)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())
            return items, total

    async def get_stats(self) -> dict:
        async with self.db.session() as session:
            result = await session.execute(
                select(ProcessingQueue.status, func.count()).group_by(ProcessingQueue.status)
            )
            by_status = {row[0]: row[1] for row in result.all()}
            total_pending = sum(v for k, v in by_status.items() if k not in ("preprocessed", "error"))
            return {"by_status": by_status, "total_pending": total_pending, "avg_processing_time": None}

    async def reprioritize(self, item_ids: list[uuid.UUID], new_priority: int) -> int:
        async with self.db.session() as session:
            result = await session.execute(
                select(ProcessingQueue).where(ProcessingQueue.id.in_(item_ids))
            )
            entries = result.scalars().all()
            for e in entries:
                e.priority = new_priority
                session.add(e)
            await session.flush()
            return len(entries)
