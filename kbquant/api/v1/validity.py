from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from kbquant.api.dependencies import get_validity_service, get_read_validity_service, verify_api_key
from kbquant.schemas.validity import (
    TimeValidityCreate, TimeValidityResponse,
    ValidityExpireRequest, ValidityExtendRequest, ValidityCheckResponse,
)
from kbquant.services.validity_service import ValidityService

router = APIRouter(prefix="/validity", tags=["validity"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=TimeValidityResponse, status_code=201)
async def create_validity(data: TimeValidityCreate, svc: ValidityService = Depends(get_validity_service)):
    return await svc.create(**data.model_dump())


@router.get("/", response_model=list[TimeValidityResponse])
async def list_validity(target_type: str | None = None, expired: bool | None = None,
                        svc: ValidityService = Depends(get_read_validity_service)):
    return await svc.list_items(target_type=target_type, expired=expired)


@router.put("/{validity_id}/expire", response_model=TimeValidityResponse)
async def expire_validity(validity_id: UUID, data: ValidityExpireRequest,
                          svc: ValidityService = Depends(get_validity_service)):
    result = await svc.expire(validity_id, data.invalidation_reason, data.invalidation_evidence_id)
    if not result:
        raise HTTPException(404, "Validity entry not found")
    return result


@router.put("/{validity_id}/extend", response_model=TimeValidityResponse)
async def extend_validity(validity_id: UUID, data: ValidityExtendRequest,
                          svc: ValidityService = Depends(get_validity_service)):
    result = await svc.extend(validity_id, data.new_valid_until)
    if not result:
        raise HTTPException(404, "Validity entry not found")
    return result


@router.get("/check", response_model=ValidityCheckResponse)
async def check_validity(target_type: str, target_id: str, svc: ValidityService = Depends(get_read_validity_service)):
    return await svc.check(target_type, target_id)
