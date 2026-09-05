from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from kbquant.api.dependencies import (
    get_information_service,
    get_read_information_service,
    get_entity_service,
    get_read_entity_service,
    verify_api_key,
)
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.common import BatchGetRequest
from kbquant.schemas.entity import EntityExtractRequest
from kbquant.schemas.information import (
    BatchUpdateImportanceRequest,
    BatchUpdateImportanceResponse,
    DedupCheckRequest,
    DedupCheckResponse,
    InformationMergeRequest,
    InformationMergeResponse,
    RawInformationCreate,
    RawInformationResponse,
)
from kbquant.services.entity_service import EntityService
from kbquant.services.information_service import InformationService

router = APIRouter(prefix="/information", tags=["information"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=RawInformationResponse, status_code=201)
async def ingest_information(
    data: RawInformationCreate,
    service: InformationService = Depends(get_information_service),
):
    info = await service.ingest(
        title=data.title,
        body=data.body,
        source=data.source,
        source_url=data.source_url,
        published_at=data.published_at,
        info_type=data.info_type,
        language=data.language,
        raw_metadata=data.raw_metadata,
    )
    return info


@router.put("/importance", response_model=BatchUpdateImportanceResponse)
async def batch_update_importance(
    data: BatchUpdateImportanceRequest,
    service: InformationService = Depends(get_information_service),
):
    updated = await service.batch_update_importance(data.scores)
    return BatchUpdateImportanceResponse(updated=updated)


@router.get("/{info_id}", response_model=RawInformationResponse)
async def get_information(
    info_id: UUID,
    service: InformationService = Depends(get_read_information_service),
):
    info = await service.get(info_id)
    if not info:
        raise HTTPException(status_code=404, detail="Information not found")
    return info


@router.post("/batch", response_model=list[RawInformationResponse])
async def get_information_batch(
    data: BatchGetRequest,
    service: InformationService = Depends(get_read_information_service),
):
    return await service.get_many(data.ids)


@router.get("/", response_model=PaginatedResponse)
async def list_information(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    info_type: str | None = None,
    source: str | None = None,
    status: str | None = None,
    from_date: str | None = Query(None, description="资讯发布时间起始 (ISO date, e.g. 2024-01-01)"),
    to_date: str | None = Query(None, description="资讯发布时间截止 (ISO date, e.g. 2024-12-31)"),
    entity: str | None = Query(None, description="按实体名称模糊搜索"),
    ticker: str | None = Query(None, description="按股票代码搜索"),
    service: InformationService = Depends(get_read_information_service),
):
    items, total = await service.list_items(
        page=page, page_size=page_size,
        info_type=info_type, source=source, status=status,
        from_date=from_date, to_date=to_date,
        entity=entity, ticker=ticker,
    )
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/check-duplicate", response_model=DedupCheckResponse)
async def check_duplicate(
    data: DedupCheckRequest,
    service: InformationService = Depends(get_read_information_service),
):
    result = await service.check_duplicate(data.title, data.body, threshold=data.threshold)
    return DedupCheckResponse(**result)


@router.post("/merge", response_model=InformationMergeResponse, status_code=201)
async def merge_information(
    data: InformationMergeRequest,
    service: InformationService = Depends(get_information_service),
):
    dedup = await service.merge(
        primary_id=data.primary_id,
        duplicate_id=data.duplicate_id,
        dedup_type=data.dedup_type,
        dedup_rationale=data.dedup_rationale,
    )
    return dedup


@router.post("/{info_id}/extract-entities")
async def extract_entities(info_id: UUID, data: EntityExtractRequest,
                           svc: EntityService = Depends(get_entity_service)):
    return await svc.extract_entities(info_id, data.entities)


@router.get("/{info_id}/entities")
async def get_info_entities(info_id: UUID, svc: EntityService = Depends(get_read_entity_service)):
    return await svc.get_entities_for_info(info_id)
