from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from kbquant.api.dependencies import get_read_lazy, get_read_node_service, verify_api_key
from kbquant.services.as_of_time_service import AsOfTimeService
from kbquant.services.node_service import NodeService

router = APIRouter(prefix="/queries", tags=["queries"], dependencies=[Depends(verify_api_key)])


@router.get("/as-of/{timestamp}")
async def query_as_of(timestamp: datetime, node_id: UUID | None = None,
                      entity_id: UUID | None = None, info_id: UUID | None = None,
                      db = Depends(get_read_lazy)):
    svc = AsOfTimeService(db)
    return await svc.query_at(timestamp, node_id=node_id, entity_id=entity_id, info_id=info_id)


@router.post("/as-of-diff")
async def diff_state(node_id: UUID, timestamp_a: datetime, timestamp_b: datetime,
                     db = Depends(get_read_lazy)):
    svc = AsOfTimeService(db)
    return await svc.diff_state(node_id, timestamp_a, timestamp_b)


@router.get("/nodes/{node_id}/state/at/{timestamp}")
async def get_state_at(node_id: UUID, timestamp: datetime, svc: NodeService = Depends(get_read_node_service)):
    state = await svc.get_state_at(node_id, timestamp)
    if not state:
        return {"error": "No state found at this timestamp"}
    return state
