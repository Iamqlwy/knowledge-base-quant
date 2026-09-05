from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from kbquant.api.dependencies import get_conflict_service, get_read_conflict_service, verify_api_key
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.conflict import (
    ConflictDetectRequest, ConflictDetectResponse,
    ConflictResolveRequest, ConflictResponse,
)
from kbquant.services.conflict_service import ConflictService

router = APIRouter(prefix="/conflicts", tags=["conflicts"], dependencies=[Depends(verify_api_key)])


@router.post("/detect", response_model=ConflictDetectResponse)
async def detect_conflict(data: ConflictDetectRequest, svc: ConflictService = Depends(get_conflict_service)):
    conflict = await svc.detect(**data.model_dump(exclude_none=True))
    return {"has_conflict": True, "conflict_type": conflict.conflict_type, "conflict_id": conflict.id}


@router.get("/", response_model=PaginatedResponse)
async def list_conflicts(node_id: UUID | None = None, conflict_type: str | None = None,
                         resolved: bool | None = None, page: int = Query(1, ge=1),
                         page_size: int = Query(20, ge=1, le=100),
                         svc: ConflictService = Depends(get_read_conflict_service)):
    items, total = await svc.list_items(node_id=node_id, conflict_type=conflict_type,
                                        is_resolved=resolved, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.put("/{conflict_id}/resolve", response_model=ConflictResponse)
async def resolve_conflict(conflict_id: UUID, data: ConflictResolveRequest,
                           svc: ConflictService = Depends(get_conflict_service)):
    result = await svc.resolve(conflict_id, data.resolution)
    if not result:
        raise HTTPException(404, "Conflict not found")
    return result
