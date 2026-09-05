import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func

from kbquant.database import LazyDB
from kbquant.models.time_validity import TimeValidity


class ValidityService:
    def __init__(self, db: LazyDB):
        self.db = db

    async def create(self, *, target_type: str, target_id: str,
                     valid_from: datetime, valid_until: datetime | None = None) -> TimeValidity:
        async with self.db.session() as session:
            tv = TimeValidity(
                target_type=target_type, target_id=target_id,
                valid_from=valid_from, valid_until=valid_until,
            )
            session.add(tv)
            await session.flush()
            return tv

    async def list_items(self, *, node_id: uuid.UUID | None = None, target_type: str | None = None,
                         expired: bool | None = None) -> list[TimeValidity]:
        async with self.db.session() as session:
            query = select(TimeValidity)
            if target_type:
                query = query.where(TimeValidity.target_type == target_type)
            if expired is True:
                query = query.where(TimeValidity.valid_until <= datetime.now(timezone.utc))
            elif expired is False:
                query = query.where(
                    (TimeValidity.valid_until == None) |
                    (TimeValidity.valid_until > datetime.now(timezone.utc))
                )
            result = await session.execute(query.limit(100))
            return list(result.scalars().all())

    async def expire(self, validity_id: uuid.UUID, reason: str | None = None,
                     evidence_id: uuid.UUID | None = None) -> TimeValidity | None:
        async with self.db.session() as session:
            result = await session.execute(select(TimeValidity).where(TimeValidity.id == validity_id))
            tv = result.scalar_one_or_none()
            if not tv:
                return None
            tv.valid_until = datetime.now(timezone.utc)
            tv.invalidation_reason = reason
            tv.invalidation_evidence_id = evidence_id
            session.add(tv)
            await session.flush()
            return tv

    async def extend(self, validity_id: uuid.UUID, new_valid_until: datetime) -> TimeValidity | None:
        async with self.db.session() as session:
            result = await session.execute(select(TimeValidity).where(TimeValidity.id == validity_id))
            tv = result.scalar_one_or_none()
            if not tv:
                return None
            tv.valid_until = new_valid_until
            tv.extended_count = (tv.extended_count or 0) + 1
            session.add(tv)
            await session.flush()
            return tv

    async def check(self, target_type: str, target_id: str,
                    at_time: datetime | None = None) -> dict:
        check_time = at_time or datetime.now(timezone.utc)
        async with self.db.session() as session:
            result = await session.execute(
                select(TimeValidity).where(
                    TimeValidity.target_type == target_type,
                    TimeValidity.target_id == target_id,
                    TimeValidity.valid_from <= check_time,
                    (TimeValidity.valid_until == None) | (TimeValidity.valid_until > check_time),
                )
            )
            tv = result.scalar_one_or_none()
            return {"is_valid": tv is not None, "validity_entry": tv, "reason": None if tv else "expired"}
