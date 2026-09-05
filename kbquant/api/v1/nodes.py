from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from kbquant.api.dependencies import get_node_service, get_read_node_service, verify_api_key
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.common import BatchGetRequest
from kbquant.schemas.node import (
    WorldNodeCreate, WorldNodeResponse,
    NodeStateCreate, NodeStateResponse,
    NodeAttachmentCreate, NodeAttachmentResponse,
    NodeCompressionRequest, NodeCompressionResponse,
    NodeNameAliases,
    WorldNodeEdgeCreate, WorldNodeEdgeResponse,
)

from kbquant.services.node_service import NodeService

router = APIRouter(prefix="/nodes", tags=["nodes"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=WorldNodeResponse, status_code=201)
async def create_node(data: WorldNodeCreate, svc: NodeService = Depends(get_node_service)):
    return await svc.create_node(**data.model_dump(exclude_none=True))


@router.get("/", response_model=PaginatedResponse)
async def list_nodes(node_type: str | None = None, page: int = Query(1, ge=1),
                     page_size: int = Query(20, ge=1, le=100),
                     svc: NodeService = Depends(get_read_node_service)):
    items, total = await svc.list_nodes(node_type=node_type, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/names-aliases", response_model=list[NodeNameAliases])
async def list_node_names_and_aliases(svc: NodeService = Depends(get_read_node_service)):
    return await svc.list_node_names_and_aliases()


@router.get("/{node_id}", response_model=WorldNodeResponse)
async def get_node(node_id: UUID, svc: NodeService = Depends(get_read_node_service)):
    node = await svc.get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return node


@router.post("/batch", response_model=list[WorldNodeResponse])
async def get_nodes_batch(data: BatchGetRequest, svc: NodeService = Depends(get_read_node_service)):
    return await svc.get_nodes_many(data.ids)


@router.post("/{node_id}/attachments", response_model=NodeAttachmentResponse, status_code=201)
async def attach_to_node(node_id: UUID, data: NodeAttachmentCreate, svc: NodeService = Depends(get_node_service)):
    return await svc.attach(node_id=node_id, **data.model_dump())


@router.get("/{node_id}/attachments", response_model=list[NodeAttachmentResponse])
async def get_attachments(node_id: UUID, role: str | None = None,
                          attachment_type: str | None = None,
                          svc: NodeService = Depends(get_read_node_service)):
    return await svc.get_attachments(node_id, role=role, attachment_type=attachment_type)


@router.get("/{node_id}/state/current", response_model=NodeStateResponse)
async def get_current_state(node_id: UUID, svc: NodeService = Depends(get_read_node_service)):
    state = await svc.get_current_state(node_id)
    if not state:
        raise HTTPException(404, "No state found for this node")
    return state


@router.post("/{node_id}/state", response_model=NodeStateResponse, status_code=201)
async def update_state(node_id: UUID, data: NodeStateCreate, svc: NodeService = Depends(get_node_service)):
    return await svc.update_state(node_id, **data.model_dump(exclude_none=True))


@router.get("/{node_id}/state/history", response_model=list[NodeStateResponse])
async def get_state_history(node_id: UUID, svc: NodeService = Depends(get_read_node_service)):
    return await svc.get_state_history(node_id)


@router.post("/{node_id}/compress", response_model=NodeCompressionResponse)
async def compress_node(node_id: UUID, data: NodeCompressionRequest | None = None,
                        svc: NodeService = Depends(get_node_service)):
    result = await svc.compress(node_id, force=data.force if data else False,
                                target_ratio=data.target_compression_ratio if data else 0.3)
    if not result:
        raise HTTPException(404, "Node not found")
    return result


# --- Edge CRUD ---

@router.post("/edges", response_model=WorldNodeEdgeResponse, status_code=201)
async def add_edge(data: WorldNodeEdgeCreate, svc: NodeService = Depends(get_node_service)):
    return await svc.add_edge(
        parent_node_id=data.parent_node_id,
        child_node_id=data.child_node_id,
        relationship_type=data.relationship_type,
        weight=data.weight,
        evidence_ids=data.evidence_ids,
    )


@router.get("/{node_id}/edges", response_model=list[WorldNodeEdgeResponse])
async def get_edges_of_node(node_id: UUID, svc: NodeService = Depends(get_read_node_service)):
    parents = await svc.get_parents(node_id)
    children = await svc.get_children(node_id)
    return parents + children


@router.delete("/edges/{edge_id}", status_code=204)
async def remove_edge(edge_id: UUID, svc: NodeService = Depends(get_node_service)):
    ok = await svc.remove_edge(edge_id)
    if not ok:
        raise HTTPException(404, "Edge not found")
