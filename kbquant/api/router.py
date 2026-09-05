from fastapi import APIRouter

from kbquant.api.v1.information import router as information_router
from kbquant.api.v1.entities import router as entities_router
from kbquant.api.v1.nodes import router as nodes_router
from kbquant.api.v1.analysis import router as analysis_router
from kbquant.api.v1.trading import router as trading_router
from kbquant.api.v1.feedback import router as feedback_router
from kbquant.api.v1.search import router as search_router
from kbquant.api.v1.pipeline import router as pipeline_router
from kbquant.api.v1.validity import router as validity_router
from kbquant.api.v1.conflicts import router as conflicts_router
from kbquant.api.v1.ranking import router as ranking_router
from kbquant.api.v1.evidence import router as evidence_router
from kbquant.api.v1.queries import router as queries_router
from kbquant.api.v1.macro_report import router as macro_report_router
from kbquant.api.v1.preference import router as preference_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(information_router)
api_router.include_router(entities_router)
api_router.include_router(nodes_router)
api_router.include_router(analysis_router)
api_router.include_router(trading_router)
api_router.include_router(feedback_router)
api_router.include_router(search_router)
api_router.include_router(pipeline_router)
api_router.include_router(validity_router)
api_router.include_router(conflicts_router)
api_router.include_router(ranking_router)
api_router.include_router(evidence_router)
api_router.include_router(queries_router)
api_router.include_router(macro_report_router)
api_router.include_router(preference_router)
