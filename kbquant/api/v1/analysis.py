from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from kbquant.api.dependencies import get_analysis_service, get_read_analysis_service, verify_api_key
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.analysis import AnalysisCreate, AnalysisResponse
from kbquant.schemas.common import BatchGetRequest
from kbquant.services.analysis_service import AnalysisService

router = APIRouter(prefix="/analysis", tags=["analysis"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=AnalysisResponse, status_code=201)
async def create_analysis(data: AnalysisCreate, svc: AnalysisService = Depends(get_analysis_service)):
    return await svc.create(**data.model_dump(exclude_none=True))


@router.get("/", response_model=PaginatedResponse)
async def list_analysis(
    analysis_type: str | None = None, agent_id: str | None = None,
    confidence_min: float | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    svc: AnalysisService = Depends(get_read_analysis_service),
):
    items, total = await svc.search(analysis_type=analysis_type, agent_id=agent_id,
                                    confidence_min=confidence_min, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(analysis_id: UUID, svc: AnalysisService = Depends(get_read_analysis_service)):
    a = await svc.get(analysis_id)
    if not a:
        raise HTTPException(404, "Analysis not found")
    return a


@router.post("/batch", response_model=list[AnalysisResponse])
async def get_analysis_batch(data: BatchGetRequest,
                             svc: AnalysisService = Depends(get_read_analysis_service)):
    return await svc.get_many(data.ids)
