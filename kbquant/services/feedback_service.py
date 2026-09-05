import uuid
import asyncio

from sqlalchemy import select, func
from sqlalchemy.orm import defer

from kbquant.database import LazyDB
from kbquant.models.feedback import Feedback


class FeedbackService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def create(self, **kwargs) -> Feedback:
        custom_time = kwargs.pop("custom_time", None)
        async with self.db.session() as session:
            feedback = Feedback(**kwargs)
            if custom_time is not None:
                feedback.created_at = custom_time
                feedback.updated_at = custom_time
            session.add(feedback)
            await session.flush()
            feedback_id = feedback.id
            parts = [
                feedback.title or "",
                feedback.lessons_learned or "",
                feedback.error_reason or "",
                feedback.adjustment_suggestions or "",
            ]
            embed_text = " ".join(filter(None, parts))

        # ES sync outside bg DB session (uses its own HTTP client pool)
        import asyncio
        from kbquant.integrations.elasticsearch.sync import sync_feedback
        from kbquant.services.information_service import _track_bg_task, _get_bg_write_semaphore
        es_task = asyncio.create_task(sync_feedback(feedback))
        _track_bg_task(es_task)

        # Embedding: HTTP call outside semaphore, only DB write inside
        if embed_text.strip():
            async def _store_embedding():
                from kbquant.services.embedding_service import generate_embedding_for
                from kbquant.database import bg_write_async_session
                import logging as _logging
                _logger = _logging.getLogger(__name__)
                try:
                    vector = await generate_embedding_for(embed_text)
                except Exception:
                    _logger.warning("Embedding call failed for feedback_id=%s", feedback_id)
                    return
                try:
                    async with _get_bg_write_semaphore():
                        async with bg_write_async_session() as bg_session:
                            feedback_obj = await bg_session.get(Feedback, feedback_id)
                            if feedback_obj:
                                feedback_obj.embedding = vector
                                await bg_session.commit()
                except Exception:
                    _logger.warning("Embedding DB write failed for feedback_id=%s", feedback_id)
            _track_bg_task(asyncio.create_task(_store_embedding()))

        return feedback

    async def get(self, feedback_id: uuid.UUID) -> Feedback | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(Feedback)
                .where(Feedback.id == feedback_id)
                .options(defer(Feedback.embedding))
            )
            return result.scalar_one_or_none()

    async def get_many(self, feedback_ids: list[uuid.UUID]) -> list[Feedback]:
        if not feedback_ids:
            return []
        async with self.db.session() as session:
            result = await session.execute(
                select(Feedback)
                .where(Feedback.id.in_(feedback_ids))
                .options(defer(Feedback.embedding))
            )
            return list(result.scalars().all())

    async def list_items(self, *, judgment_correct: bool | None = None,
                         page: int = 1, page_size: int = 20) -> tuple[list[Feedback], int]:
        async with self.db.session() as session:
            query = select(Feedback)
            count_query = select(func.count()).select_from(Feedback)
            if judgment_correct is not None:
                query = query.where(Feedback.judgment_correct == judgment_correct)
                count_query = count_query.where(Feedback.judgment_correct == judgment_correct)
            query = query.order_by(Feedback.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            total_result = await session.execute(count_query)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())
            return items, total

    async def get_lessons(self, search_text: str | None = None) -> list[Feedback]:
        async with self.db.session() as session:
            query = select(Feedback).where(Feedback.lessons_learned != None)
            if search_text:
                query = query.where(Feedback.lessons_learned.ilike(f"%{search_text}%"))
            result = await session.execute(query.limit(50))
            return list(result.scalars().all())
