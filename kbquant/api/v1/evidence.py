from uuid import UUID

from fastapi import APIRouter, Depends, Query

from kbquant.api.dependencies import get_read_evidence_service, verify_api_key
from kbquant.schemas.evidence import EvidenceTraceResponse
from kbquant.services.evidence_service import EvidenceService

router = APIRouter(prefix="/evidence", tags=["evidence"], dependencies=[Depends(verify_api_key)])


@router.get("/trace/{target_type}/{target_id}", response_model=EvidenceTraceResponse)
async def trace_evidence(target_type: str, target_id: UUID, depth: int = Query(3, ge=1, le=5),
                         svc: EvidenceService = Depends(get_read_evidence_service)):
    return await svc.trace(target_type, target_id, depth=depth)


@router.get("/trace-node/{node_id}")
async def trace_node_evidence(node_id: UUID, aspect: str | None = None,
                              svc: EvidenceService = Depends(get_read_evidence_service)):
    return await svc.trace_node(node_id, aspect=aspect)
