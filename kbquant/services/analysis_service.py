import uuid
import asyncio

from sqlalchemy import select, func
from sqlalchemy.orm import defer

from kbquant.database import LazyDB
from kbquant.models.analysis import Analysis
from datetime import datetime


class AnalysisService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def create(self, *, title: str, content: str, analysis_type: str,
                     agent_id: str | None = None, confidence: float | None = None,
                     parent_analysis_id: uuid.UUID | None = None,
                     root_raw_info_ids: list[uuid.UUID] | None = None,
                     time_horizon: str | None = None,
                     custom_time: datetime | None = None) -> Analysis:
        async with self.db.session() as session:
            analysis = Analysis(
                title=title, content=content, analysis_type=analysis_type,
                agent_id=agent_id, confidence=confidence,
                parent_analysis_id=parent_analysis_id,
                root_raw_info_ids=root_raw_info_ids,
                time_horizon=time_horizon,
            )
            if custom_time is not None:
                analysis.created_at = custom_time
                analysis.updated_at = custom_time
            session.add(analysis)
            await session.flush()
            analysis_id = analysis.id
            embed_text = f"{analysis.title} {analysis.content}"

        # ES sync outside bg DB session (uses its own HTTP client pool)
        import asyncio
        from kbquant.integrations.elasticsearch.sync import sync_analysis
        from kbquant.services.information_service import _track_bg_task, _get_bg_write_semaphore
        es_task = asyncio.create_task(sync_analysis(analysis))
        _track_bg_task(es_task)

        # Embedding: HTTP call outside semaphore, only DB write inside
        async def _store_embedding():
            from kbquant.services.embedding_service import generate_embedding_for
            from kbquant.database import bg_write_async_session
            import logging as _logging
            _logger = _logging.getLogger(__name__)
            try:
                vector = await generate_embedding_for(embed_text)
            except Exception:
                _logger.warning("Embedding call failed for analysis_id=%s", analysis_id)
                return
            try:
                async with _get_bg_write_semaphore():
                    async with bg_write_async_session() as bg_session:
                        analysis_obj = await bg_session.get(Analysis, analysis_id)
                        if analysis_obj:
                            analysis_obj.embedding = vector
                            await bg_session.commit()
            except Exception:
                _logger.warning("Embedding DB write failed for analysis_id=%s", analysis_id)
        _track_bg_task(asyncio.create_task(_store_embedding()))

        return analysis

    async def get(self, analysis_id: uuid.UUID) -> Analysis | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(Analysis)
                .where(Analysis.id == analysis_id)
                .options(defer(Analysis.embedding))
            )
            return result.scalar_one_or_none()

    async def get_many(self, analysis_ids: list[uuid.UUID]) -> list[Analysis]:
        if not analysis_ids:
            return []
        async with self.db.session() as session:
            result = await session.execute(
                select(Analysis)
                .where(Analysis.id.in_(analysis_ids))
                .options(defer(Analysis.embedding))
            )
            return list(result.scalars().all())

    async def search(self, *, entity_id: uuid.UUID | None = None, node_id: uuid.UUID | None = None,
                     analysis_type: str | None = None, agent_id: str | None = None,
                     search_text: str | None = None, confidence_min: float | None = None,
                     page: int = 1, page_size: int = 20) -> tuple[list[Analysis], int]:
        async with self.db.session() as session:
            query = select(Analysis)
            count_query = select(func.count()).select_from(Analysis)
            if analysis_type:
                query = query.where(Analysis.analysis_type == analysis_type)
                count_query = count_query.where(Analysis.analysis_type == analysis_type)
            if agent_id:
                query = query.where(Analysis.agent_id == agent_id)
                count_query = count_query.where(Analysis.agent_id == agent_id)
            if confidence_min is not None:
                query = query.where(Analysis.confidence >= confidence_min)
                count_query = count_query.where(Analysis.confidence >= confidence_min)
            total_result = await session.execute(count_query)
            query = query.order_by(Analysis.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())
            return items, total
