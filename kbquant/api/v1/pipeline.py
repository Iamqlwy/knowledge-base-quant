from uuid import UUID

from fastapi import APIRouter, Depends, Query

from kbquant.api.dependencies import get_pipeline_service, get_read_pipeline_service, verify_api_key
from kbquant.schemas.pipeline import (
    PipelineStatusUpdate, PipelineStats, ReprioritizeRequest,
)
from kbquant.services.pipeline_service import PipelineService

router = APIRouter(prefix="/pipeline", tags=["pipeline"], dependencies=[Depends(verify_api_key)])


@router.get("/queue")
async def list_queue(status: list[str] | None = Query(default=None), priority_min: int | None = None,
                     agent_assigned: str | None = None, page: int = 1, page_size: int = 20,
                     svc: PipelineService = Depends(get_read_pipeline_service)):
    items, total = await svc.list_queue(status=status, priority_min=priority_min,
                                        agent_assigned=agent_assigned, page=page, page_size=page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.put("/{raw_info_id}/status")
async def update_status(raw_info_id: UUID, data: PipelineStatusUpdate,
                        svc: PipelineService = Depends(get_pipeline_service)):
    return await svc.update_status(raw_info_id, data.status, data.detail, data.priority)


@router.get("/stats", response_model=PipelineStats)
async def pipeline_stats(svc: PipelineService = Depends(get_read_pipeline_service)):
    return await svc.get_stats()


@router.post("/reprioritize")
async def reprioritize(data: ReprioritizeRequest, svc: PipelineService = Depends(get_pipeline_service)):
    count = await svc.reprioritize(data.item_ids, data.new_priority)
    return {"updated": count}
