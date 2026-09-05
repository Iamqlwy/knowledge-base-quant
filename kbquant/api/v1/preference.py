from fastapi import APIRouter, Depends, HTTPException

from kbquant.api.dependencies import get_preference_service, verify_api_key
from kbquant.schemas.preference import (
    IndustryCognitionAppend,
    IndustryCognitionAppendResponse,
    IndustryCognitionResponse,
    IndustryCognitionSectorsResponse,
    MarketCognitionAppend,
    MarketCognitionAppendResponse,
    MarketCognitionResponse,
    StructuredPreferencesResponse,
    StructuredPreferencesUpdate,
    SuggestionsPayload,
    SuggestionsResponse,
)
from kbquant.services.preference_service import PreferenceService

router = APIRouter(
    prefix="/preferences",
    tags=["preferences"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/sectors", response_model=IndustryCognitionSectorsResponse)
async def get_all_sectors(
    svc: PreferenceService = Depends(get_preference_service),
):
    sectors = await svc.get_all_sectors()
    return IndustryCognitionSectorsResponse(sectors=sectors)


@router.get("/market/cognition", response_model=MarketCognitionResponse)
async def get_market_cognition(
    svc: PreferenceService = Depends(get_preference_service),
):
    row = await svc.get_market_cognition()
    if row is None:
        return MarketCognitionResponse(text="", append_count=0)
    return MarketCognitionResponse(text=row.cognition_text, append_count=row.append_count)


@router.post("/market/cognition", response_model=MarketCognitionAppendResponse)
async def append_market_cognition(
    data: MarketCognitionAppend,
    svc: PreferenceService = Depends(get_preference_service),
):
    status = await svc.append_market_cognition(data.text, custom_time=data.custom_time)
    return MarketCognitionAppendResponse(status=status)


@router.get("/{sector}/cognition", response_model=IndustryCognitionResponse)
async def get_industry_cognition(
    sector: str,
    svc: PreferenceService = Depends(get_preference_service),
):
    row = await svc.get_industry_cognition(sector)
    if row is None:
        return IndustryCognitionResponse(sector=sector, text="", append_count=0)
    return IndustryCognitionResponse(
        sector=row.sector,
        text=row.cognition_text,
        append_count=row.append_count,
    )


@router.post("/{sector}/cognition", response_model=IndustryCognitionAppendResponse)
async def append_industry_cognition(
    sector: str,
    data: IndustryCognitionAppend,
    svc: PreferenceService = Depends(get_preference_service),
):
    s, status = await svc.append_industry_cognition(sector, data.text, custom_time=data.custom_time)
    return IndustryCognitionAppendResponse(sector=s, status=status)


@router.get("/structured", response_model=StructuredPreferencesResponse)
async def get_structured_preferences(
    svc: PreferenceService = Depends(get_preference_service),
):
    return await svc.get_structured()


@router.put("/structured", response_model=StructuredPreferencesResponse)
async def update_structured_preferences(
    data: StructuredPreferencesUpdate,
    svc: PreferenceService = Depends(get_preference_service),
):
    kwargs = data.model_dump(exclude_none=True)
    custom_time = kwargs.pop("custom_time", None)
    await svc.update_structured(custom_time=custom_time, **kwargs)
    return await svc.get_structured()


@router.post("/suggestions", response_model=SuggestionsResponse)
async def apply_suggestions(
    data: SuggestionsPayload,
    svc: PreferenceService = Depends(get_preference_service),
):
    payload = data.model_dump()
    custom_time = payload.pop("custom_time", None)
    return await svc.apply_suggestions(payload, custom_time=custom_time)
