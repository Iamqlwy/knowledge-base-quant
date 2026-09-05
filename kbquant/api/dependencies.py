import logging
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from kbquant.config import settings
from kbquant.database import (
    write_async_session, read_async_session, read_lazy, write_lazy, LazyDB,
)
from kbquant.services.analysis_service import AnalysisService
from kbquant.services.conflict_service import ConflictService
from kbquant.services.entity_service import EntityService
from kbquant.services.evidence_service import EvidenceService
from kbquant.services.feedback_service import FeedbackService
from kbquant.services.information_service import InformationService
from kbquant.services.node_service import NodeService
from kbquant.services.pipeline_service import PipelineService
from kbquant.services.ranking_service import RankingService
from kbquant.services.search_service import SearchService
from kbquant.services.trading_service import TradingService
from kbquant.services.macro_report_service import MacroReportService
from kbquant.services.preference_service import PreferenceService
from kbquant.services.validity_service import ValidityService

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    expected = settings.api_key
    if not expected:
        return "no-auth"
    if api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


# ── Legacy helpers for endpoints that still use raw AsyncSession ──

async def _session_scope(session_factory, *, commit: bool) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        try:
            yield session
            if commit:
                await session.commit()
            else:
                await session.rollback()
        except HTTPException:
            await session.rollback()
            raise
        except Exception:
            logger.exception("Request failed, rolling back transaction")
            await session.rollback()
            raise


async def get_write_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in _session_scope(write_async_session, commit=True):
        yield session


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in _session_scope(read_async_session, commit=False):
        yield session


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        async for session in get_read_db():
            yield session
    else:
        async for session in get_write_db():
            yield session


# ── Lazy session factory injectors ──
# Services receive a LazyDB wrapper.  Methods open short-lived sessions
# only when they actually need the database, releasing the connection
# immediately afterward instead of hoarding it for the entire request.

def get_read_lazy() -> LazyDB:
    return read_lazy


def get_write_lazy() -> LazyDB:
    return write_lazy


# ── Lazy-injected service providers (read paths) ──

async def get_read_search_service(db: LazyDB = Depends(get_read_lazy)) -> SearchService:
    return SearchService(db)


async def get_read_information_service(db: LazyDB = Depends(get_read_lazy)) -> InformationService:
    return InformationService(db)


async def get_read_entity_service(db: LazyDB = Depends(get_read_lazy)) -> EntityService:
    return EntityService(db)


async def get_read_analysis_service(db: LazyDB = Depends(get_read_lazy)) -> AnalysisService:
    return AnalysisService(db)


async def get_read_feedback_service(db: LazyDB = Depends(get_read_lazy)) -> FeedbackService:
    return FeedbackService(db)


async def get_read_node_service(db: LazyDB = Depends(get_read_lazy)) -> NodeService:
    return NodeService(db)


async def get_read_conflict_service(db: LazyDB = Depends(get_read_lazy)) -> ConflictService:
    return ConflictService(db)


async def get_read_evidence_service(db: LazyDB = Depends(get_read_lazy)) -> EvidenceService:
    return EvidenceService(db)


async def get_read_pipeline_service(db: LazyDB = Depends(get_read_lazy)) -> PipelineService:
    return PipelineService(db)


async def get_read_ranking_service(db: LazyDB = Depends(get_read_lazy)) -> RankingService:
    return RankingService(db)


async def get_read_trading_service(db: LazyDB = Depends(get_read_lazy)) -> TradingService:
    return TradingService(db)


async def get_read_validity_service(db: LazyDB = Depends(get_read_lazy)) -> ValidityService:
    return ValidityService(db)


async def get_read_macro_report_service(db: LazyDB = Depends(get_read_lazy)) -> MacroReportService:
    return MacroReportService(db)


async def get_read_preference_service(db: LazyDB = Depends(get_read_lazy)) -> PreferenceService:
    return PreferenceService(db)


# ── Lazy-injected service providers (write paths — still lazy, commit=True) ──

async def get_search_service(db: LazyDB = Depends(get_write_lazy)) -> SearchService:
    return SearchService(db)


async def get_information_service(db: LazyDB = Depends(get_write_lazy)) -> InformationService:
    return InformationService(db)


async def get_entity_service(db: LazyDB = Depends(get_write_lazy)) -> EntityService:
    return EntityService(db)


async def get_analysis_service(db: LazyDB = Depends(get_write_lazy)) -> AnalysisService:
    return AnalysisService(db)


async def get_feedback_service(db: LazyDB = Depends(get_write_lazy)) -> FeedbackService:
    return FeedbackService(db)


async def get_node_service(db: LazyDB = Depends(get_write_lazy)) -> NodeService:
    return NodeService(db)


async def get_conflict_service(db: LazyDB = Depends(get_write_lazy)) -> ConflictService:
    return ConflictService(db)


async def get_evidence_service(db: LazyDB = Depends(get_write_lazy)) -> EvidenceService:
    return EvidenceService(db)


async def get_pipeline_service(db: LazyDB = Depends(get_write_lazy)) -> PipelineService:
    return PipelineService(db)


async def get_ranking_service(db: LazyDB = Depends(get_write_lazy)) -> RankingService:
    return RankingService(db)


async def get_trading_service(db: LazyDB = Depends(get_write_lazy)) -> TradingService:
    return TradingService(db)


async def get_validity_service(db: LazyDB = Depends(get_write_lazy)) -> ValidityService:
    return ValidityService(db)


async def get_macro_report_service(db: LazyDB = Depends(get_write_lazy)) -> MacroReportService:
    return MacroReportService(db)


async def get_preference_service(db: LazyDB = Depends(get_write_lazy)) -> PreferenceService:
    return PreferenceService(db)
