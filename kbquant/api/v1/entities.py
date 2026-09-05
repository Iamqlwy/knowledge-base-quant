from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from kbquant.api.dependencies import get_read_lazy, get_read_entity_service, get_entity_service, verify_api_key
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.entity import (
    EntityCreate, EntityResponse, EntityExtractRequest,
    EntityRelationshipCreate, EntityRelationshipResponse,
    ImpactPathResponse,
)
from kbquant.services.entity_service import EntityService
from kbquant.services.impact_path_service import ImpactPathService

router = APIRouter(prefix="/entities", tags=["entities"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=EntityResponse, status_code=201)
async def create_entity(data: EntityCreate, svc: EntityService = Depends(get_entity_service)):
    return await svc.create_entity(
        name=data.name, entity_type=data.entity_type,
        aliases=data.aliases, metadata_=data.metadata_,
        linked_node_id=data.linked_node_id,
    )


@router.get("/", response_model=PaginatedResponse)
async def list_entities(
    entity_type: str | None = None, search: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    svc: EntityService = Depends(get_read_entity_service),
):
    items, total = await svc.list_entities(entity_type=entity_type, search=search, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/relationships", response_model=EntityRelationshipResponse, status_code=201)
async def create_relationship(data: EntityRelationshipCreate, svc: EntityService = Depends(get_entity_service)):
    return await svc.upsert_relationship(**data.model_dump(exclude_none=True))


@router.get("/{entity_id}/relationships", response_model=list[EntityRelationshipResponse])
async def get_relationships(entity_id: UUID, svc: EntityService = Depends(get_read_entity_service)):
    return await svc.get_relationships(entity_id)


@router.get("/impact-path/{entity_id}", response_model=ImpactPathResponse)
async def impact_path(entity_id: UUID, depth: int = Query(3, ge=1, le=5),
                      direction: str = Query("downstream"),
                      db = Depends(get_read_lazy)):
    svc = ImpactPathService(db)
    return await svc.find_paths(entity_id, depth=depth, direction=direction)
