import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func

from kbquant.database import LazyDB
from kbquant.models.trading_operation import TradingOperation


class TradingService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def create(self, **kwargs) -> TradingOperation:
        custom_time = kwargs.pop("custom_time", None)
        async with self.db.session() as session:
            op = TradingOperation(**kwargs)
            if custom_time is not None:
                op.created_at = custom_time
                op.updated_at = custom_time
            session.add(op)
            await session.flush()
            return op

    async def get(self, trade_id: uuid.UUID) -> TradingOperation | None:
        async with self.db.session() as session:
            result = await session.execute(select(TradingOperation).where(TradingOperation.id == trade_id))
            return result.scalar_one_or_none()

    async def get_many(self, trade_ids: list[uuid.UUID]) -> list[TradingOperation]:
        if not trade_ids:
            return []
        async with self.db.session() as session:
            result = await session.execute(
                select(TradingOperation).where(TradingOperation.id.in_(trade_ids))
            )
            return list(result.scalars().all())

    async def update(self, trade_id: uuid.UUID, *, status: str, reason: str | None = None,
                     custom_time: datetime | None = None) -> TradingOperation | None:
        async with self.db.session() as session:
            result = await session.execute(select(TradingOperation).where(TradingOperation.id == trade_id))
            op = result.scalar_one_or_none()
            if not op:
                return None
            op.status = status
            if reason:
                op.rationale = reason
            if status == "approved":
                op.executed_at = custom_time if custom_time is not None else datetime.now(timezone.utc)
            if custom_time is not None:
                op.updated_at = custom_time
            await session.flush()
            return op

    async def search(self, *, operation_type: str | None = None, node_id: uuid.UUID | None = None,
                     symbol: str | None = None, status: str | None = None,
                     page: int = 1, page_size: int = 20) -> tuple[list[TradingOperation], int]:
        async with self.db.session() as session:
            query = select(TradingOperation)
            count_query = select(func.count()).select_from(TradingOperation)
            if operation_type:
                query = query.where(TradingOperation.operation_type == operation_type)
                count_query = count_query.where(TradingOperation.operation_type == operation_type)
            if node_id:
                query = query.where(TradingOperation.target_node_id == node_id)
                count_query = count_query.where(TradingOperation.target_node_id == node_id)
            if symbol:
                query = query.where(TradingOperation.symbol == symbol)
                count_query = count_query.where(TradingOperation.symbol == symbol)
            if status:
                query = query.where(TradingOperation.status == status)
                count_query = count_query.where(TradingOperation.status == status)
            query = query.order_by(TradingOperation.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            total_result = await session.execute(count_query)
            data_result = await session.execute(query)
            total = total_result.scalar_one()
            items = list(data_result.scalars().all())
            return items, total
