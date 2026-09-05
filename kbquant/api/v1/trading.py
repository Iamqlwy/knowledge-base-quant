from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from kbquant.api.dependencies import get_trading_service, get_read_trading_service, verify_api_key
from kbquant.schemas import PaginatedResponse
from kbquant.schemas.common import BatchGetRequest
from kbquant.schemas.trading import TradingOperationCreate, TradingOperationResponse, TradingOperationUpdate
from kbquant.services.trading_service import TradingService

router = APIRouter(prefix="/trading", tags=["trading"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=TradingOperationResponse, status_code=201)
async def create_trade(data: TradingOperationCreate, svc: TradingService = Depends(get_trading_service)):
    return await svc.create(**data.model_dump(exclude_none=True))


@router.get("/", response_model=PaginatedResponse)
async def list_trades(
    operation_type: str | None = None, node_id: UUID | None = None,
    symbol: str | None = None, status: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    svc: TradingService = Depends(get_read_trading_service),
):
    items, total = await svc.search(operation_type=operation_type, node_id=node_id,
                                    symbol=symbol, status=status, page=page, page_size=page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{trade_id}", response_model=TradingOperationResponse)
async def get_trade(trade_id: UUID, svc: TradingService = Depends(get_read_trading_service)):
    t = await svc.get(trade_id)
    if not t:
        raise HTTPException(404, "Trade not found")
    return t


@router.post("/batch", response_model=list[TradingOperationResponse])
async def get_trade_batch(data: BatchGetRequest,
                          svc: TradingService = Depends(get_read_trading_service)):
    return await svc.get_many(data.ids)


@router.put("/{trade_id}", response_model=TradingOperationResponse)
async def update_trade(trade_id: UUID, data: TradingOperationUpdate,
                       svc: TradingService = Depends(get_trading_service)):
    t = await svc.update(trade_id, status=data.status, reason=data.reason, custom_time=data.custom_time)
    if not t:
        raise HTTPException(404, "Trade not found")
    return t
