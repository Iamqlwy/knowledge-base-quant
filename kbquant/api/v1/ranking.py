from uuid import UUID

from fastapi import APIRouter, Depends, Query

from kbquant.api.dependencies import get_ranking_service, get_read_ranking_service, verify_api_key
from kbquant.schemas.ranking import (
    RankingComputeRequest, RankingComputeResponse,
    RankingHistoryResponse,
)
from kbquant.services.ranking_service import RankingService

router = APIRouter(prefix="/ranking", tags=["ranking"], dependencies=[Depends(verify_api_key)])


@router.post("/compute", response_model=RankingComputeResponse)
async def compute_ranking(data: RankingComputeRequest, svc: RankingService = Depends(get_ranking_service)):
    return await svc.compute(data.target_type, data.target_id, data.force_recompute)


@router.get("/")
async def list_rankings(target_type: str | None = None, min_score: float | None = None,
                        limit: int = Query(20, le=100), svc: RankingService = Depends(get_read_ranking_service)):
    return await svc.list_rankings(target_type=target_type, min_score=min_score, limit=limit)


@router.get("/history/{target_type}/{target_id}", response_model=list[RankingHistoryResponse])
async def get_ranking_history(target_type: str, target_id: UUID, svc: RankingService = Depends(get_read_ranking_service)):
    return await svc.get_history(target_type, target_id)
