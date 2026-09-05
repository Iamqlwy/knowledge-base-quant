import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from kbquant.database import LazyDB, bg_write_async_session
from kbquant.integrations.elasticsearch.sync import sync_raw_info
from kbquant.models.raw_information import RawInformation
from kbquant.models.information_dedup import InformationDedup
from kbquant.utils.hashing import compute_content_hash

logger = logging.getLogger(__name__)

_pending_tasks: set[asyncio.Task] = set()
_background_write_semaphore: asyncio.Semaphore | None = None
_embedding_concurrency_semaphore: asyncio.Semaphore | None = None


def _get_embedding_semaphore() -> asyncio.Semaphore:
    global _embedding_concurrency_semaphore
    if _embedding_concurrency_semaphore is None:
        _embedding_concurrency_semaphore = asyncio.Semaphore(200)
    return _embedding_concurrency_semaphore


def _get_bg_write_semaphore() -> asyncio.Semaphore:
    global _background_write_semaphore
    if _background_write_semaphore is None:
        _background_write_semaphore = asyncio.Semaphore(3)
    return _background_write_semaphore


def _track_bg_task(task: asyncio.Task) -> None:
    def _done(t: asyncio.Task) -> None:
        _pending_tasks.discard(t)
        try:
            t.result()
        except Exception:
            logger.exception("后台任务失败: %s", t.get_name())

    _pending_tasks.add(task)
    task.add_done_callback(_done)


async def drain_background_tasks(timeout: float = 10.0) -> None:
    if not _pending_tasks:
        return
    n = len(_pending_tasks)
    # Scale timeout with pending task count: at least 15s, up to 120s
    effective_timeout = max(15.0, min(n * 0.3, 120.0))
    logger.info("等待 %d 个后台任务完成 (timeout=%.1fs)...", n, effective_timeout)
    gathered = asyncio.gather(*list(_pending_tasks), return_exceptions=True)
    try:
        async with asyncio.timeout(effective_timeout):
            results = await gathered
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("后台任务异常: %s", r)
    except asyncio.TimeoutError:
        logger.warning("后台任务未在 %.1fs 内完成 (%d/%d 个待处理)，强制取消", effective_timeout,
                       sum(1 for t in _pending_tasks if not t.done()), n)
    _pending_tasks.clear()


class InformationService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def ingest(self, *, title: str, body: str, source: str, published_at: datetime,
                     info_type: str, source_url: str | None = None, language: str = "zh",
                     raw_metadata: dict | None = None) -> RawInformation:
        content_hash = compute_content_hash(title, body)

        async with self.db.session() as session:
            existing = await session.execute(
                select(RawInformation).where(RawInformation.content_hash == content_hash)
            )
            if row := existing.scalar_one_or_none():
                return row

            info = RawInformation(
                title=title,
                body=body,
                source=source,
                source_url=source_url,
                published_at=published_at,
                ingested_at=datetime.now(timezone.utc),
                info_type=info_type,
                language=language,
                raw_metadata=raw_metadata or {},
                content_hash=content_hash,
            )
            session.add(info)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                async with self.db.session() as new_session:
                    existing = await new_session.execute(
                        select(RawInformation).where(RawInformation.content_hash == content_hash)
                    )
                    return existing.scalar_one()

            info_id = info.id
            embed_text = f"{info.title} {info.body}"

        # Spawn bg tasks AFTER main session commits so the row is visible
        from kbquant.services.pipeline_service import PipelineService
        pipeline_svc = PipelineService(self.db)
        await pipeline_svc.get_or_create_queue_entry(info_id)

        # ES sync: uses its own HTTP client pool, no bg DB session needed
        es_task = asyncio.create_task(sync_raw_info(info))
        _track_bg_task(es_task)

        # Embedding: HTTP call wrapped in semaphore to bound concurrent requests
        async def _store_embedding():
            from kbquant.services.embedding_service import generate_embedding_for
            try:
                async with _get_embedding_semaphore():
                    vector = await generate_embedding_for(embed_text)
            except Exception:
                logger.warning("Embedding call failed for info_id=%s", info_id)
                return
            try:
                async with _get_bg_write_semaphore():
                    async with bg_write_async_session() as bg_session:
                        info_obj = await bg_session.get(RawInformation, info_id)
                        if info_obj:
                            info_obj.embedding = vector
                            await bg_session.commit()
            except Exception:
                logger.warning("Embedding DB write failed for info_id=%s", info_id)

        embed_task = asyncio.create_task(_store_embedding())
        _track_bg_task(embed_task)

        return info

    async def get(self, info_id: uuid.UUID) -> RawInformation | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(RawInformation)
                .where(RawInformation.id == info_id)
                .options(defer(RawInformation.embedding))
            )
            return result.scalar_one_or_none()

    async def get_many(self, info_ids: list[uuid.UUID]) -> list[RawInformation]:
        if not info_ids:
            return []
        async with self.db.session() as session:
            result = await session.execute(
                select(RawInformation)
                .where(RawInformation.id.in_(info_ids))
                .options(defer(RawInformation.embedding))
            )
            return list(result.scalars().all())

    async def list_items(self, *, page: int = 1, page_size: int = 20,
                         info_type: str | None = None, source: str | None = None,
                         status: str | None = None,
                         from_date: str | None = None, to_date: str | None = None,
                         entity: str | None = None, ticker: str | None = None,
                         ) -> tuple[list[RawInformation], int]:
        from datetime import date

        query = select(RawInformation).distinct()
        count_query = select(func.count()).select_from(RawInformation)

        if from_date:
            query = query.where(RawInformation.published_at >= date.fromisoformat(from_date))
            count_query = count_query.where(RawInformation.published_at >= date.fromisoformat(from_date))
        if to_date:
            # include the whole day
            from datetime import timedelta
            end = date.fromisoformat(to_date) + timedelta(days=1)
            query = query.where(RawInformation.published_at < end)
            count_query = count_query.where(RawInformation.published_at < end)

        # Entity/ticker filter: merge into a single join + OR condition
        # to avoid joining the same tables twice when both are provided.
        if entity or ticker:
            from kbquant.models.information_entity import InformationEntity
            from kbquant.models.entity import Entity
            join_conditions = [
                InformationEntity.raw_info_id == RawInformation.id,
                InformationEntity.entity_id == Entity.id,
            ]
            filter_conditions = []
            if entity:
                escaped = entity.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                filter_conditions.append(Entity.name.ilike(f"%{escaped}%"))
            if ticker:
                escaped = ticker.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                filter_conditions.append(Entity.name.ilike(f"%{escaped}%"))
            from sqlalchemy import or_
            query = (query
                     .join(InformationEntity, join_conditions[0])
                     .join(Entity, join_conditions[1])
                     .where(or_(*filter_conditions)))
            count_query = (count_query
                           .join(InformationEntity, join_conditions[0])
                           .join(Entity, join_conditions[1])
                           .where(or_(*filter_conditions)))

        if info_type:
            query = query.where(RawInformation.info_type == info_type)
            count_query = count_query.where(RawInformation.info_type == info_type)
        if source:
            query = query.where(RawInformation.source == source)
            count_query = count_query.where(RawInformation.source == source)
        if status:
            query = query.where(RawInformation.processing_status == status)
            count_query = count_query.where(RawInformation.processing_status == status)

        async with self.db.session() as session:
            query = query.order_by(RawInformation.published_at.desc())
            query = query.offset((page - 1) * page_size).limit(page_size)
            total_result = await session.execute(count_query)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())

        return items, total

    async def check_duplicate(self, title: str, body: str, threshold: float = 0.85) -> dict:
        content_hash = compute_content_hash(title, body)

        async with self.db.session() as session:
            result = await session.execute(
                select(RawInformation).where(RawInformation.content_hash == content_hash)
            )
            if row := result.scalar_one_or_none():
                return {
                    "is_duplicate": True,
                    "primary_id": row.id,
                    "similarity_score": 1.0,
                    "matches": [{"id": row.id, "title": row.title, "similarity_score": 1.0}],
                }

        return {"is_duplicate": False, "primary_id": None, "similarity_score": None, "matches": []}

    async def merge(self, primary_id: uuid.UUID, duplicate_id: uuid.UUID,
                    dedup_type: str, dedup_rationale: str | None = None) -> InformationDedup:
        async with self.db.session() as session:
            existing = await session.execute(
                select(InformationDedup).where(
                    InformationDedup.primary_info_id == primary_id,
                    InformationDedup.duplicate_info_id == duplicate_id,
                )
            )
            if dedup := existing.scalar_one_or_none():
                return dedup

            dedup = InformationDedup(
                primary_info_id=primary_id,
                duplicate_info_id=duplicate_id,
                dedup_type=dedup_type,
                dedup_rationale=dedup_rationale,
            )
            session.add(dedup)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # Another request concurrently created the same dedup; re-query it.
                existing = await session.execute(
                    select(InformationDedup).where(
                        InformationDedup.primary_info_id == primary_id,
                        InformationDedup.duplicate_info_id == duplicate_id,
                    )
                )
                dedup = existing.scalar_one()
            return dedup

    async def get_duplicates(self, info_id: uuid.UUID) -> list[InformationDedup]:
        async with self.db.session() as session:
            result = await session.execute(
                select(InformationDedup).where(
                    (InformationDedup.primary_info_id == info_id) |
                    (InformationDedup.duplicate_info_id == info_id)
                )
            )
            return list(result.scalars().all())

    async def batch_update_importance(self, scores: dict[uuid.UUID, float]) -> int:
        if not scores:
            return 0
        async with self.db.session() as session:
            from sqlalchemy import case as _case, update as _update
            id_list = list(scores.keys())
            # 单条 UPDATE ... SET importance_score = CASE WHEN id = ? THEN ? ... END
            cases = [(RawInformation.id == uid, score) for uid, score in scores.items()]
            stmt = (
                _update(RawInformation)
                .where(RawInformation.id.in_(id_list))
                .values(importance_score=_case(*cases))
            )
            await session.execute(stmt)
            await session.commit()
            return len(scores)
