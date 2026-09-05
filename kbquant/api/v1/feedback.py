from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from kbquant.api.dependencies import get_feedback_service, get_read_feedback_service, verify_api_key
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.common import BatchGetRequest
from kbquant.schemas.feedback import FeedbackCreate, FeedbackResponse
from kbquant.services.feedback_service import FeedbackService

router = APIRouter(prefix="/feedback", tags=["feedback"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=FeedbackResponse, status_code=201)
async def create_feedback(data: FeedbackCreate, svc: FeedbackService = Depends(get_feedback_service)):
    return await svc.create(**data.model_dump(exclude_none=True))


@router.get("/", response_model=PaginatedResponse)
async def list_feedback(judgment_correct: bool | None = None,
                        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                        svc: FeedbackService = Depends(get_read_feedback_service)):
    items, total = await svc.list_items(judgment_correct=judgment_correct, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/lessons")
async def get_lessons(search_text: str | None = None, svc: FeedbackService = Depends(get_read_feedback_service)):
    return await svc.get_lessons(search_text=search_text)


@router.get("/{feedback_id}", response_model=FeedbackResponse)
async def get_feedback(feedback_id: UUID, svc: FeedbackService = Depends(get_read_feedback_service)):
    fb = await svc.get(feedback_id)
    if not fb:
        raise HTTPException(404, "Feedback not found")
    return fb


@router.post("/batch", response_model=list[FeedbackResponse])
async def get_feedback_batch(data: BatchGetRequest,
                             svc: FeedbackService = Depends(get_read_feedback_service)):
    return await svc.get_many(data.ids)
