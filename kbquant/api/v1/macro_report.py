from fastapi import APIRouter, Depends, Query

from kbquant.api.dependencies import get_macro_report_service, get_read_macro_report_service, verify_api_key
from kbquant.schemas.macro_report import (
    MacroReportHistoryResponse,
    MacroReportResponse,
    MacroReportUpdate,
)
from kbquant.services.macro_report_service import MacroReportService

router = APIRouter(prefix="/macro-report", tags=["macro-report"], dependencies=[Depends(verify_api_key)])


@router.get("/current", response_model=MacroReportResponse | None)
async def get_current_macro_report(svc: MacroReportService = Depends(get_read_macro_report_service)):
    return await svc.get_current()


@router.put("", response_model=MacroReportResponse)
async def update_macro_report(data: MacroReportUpdate,
                              svc: MacroReportService = Depends(get_macro_report_service)):
    return await svc.update(
        content=data.content,
        summary=data.summary,
        changed_sections=data.changed_sections,
        custom_time=data.custom_time,
    )


@router.get("/history", response_model=MacroReportHistoryResponse)
async def get_macro_report_history(
    limit: int = Query(default=50, ge=1, le=200),
    svc: MacroReportService = Depends(get_read_macro_report_service),
):
    items = await svc.get_history(limit=limit)
    return MacroReportHistoryResponse(items=items)
