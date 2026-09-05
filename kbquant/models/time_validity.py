import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class TimeValidity(BaseModel):
    """时效管理 —— 管理 driver/risk/focus_point 的有效期，判断逻辑是否仍然成立"""
    __tablename__ = "time_validities"

    # 目标类型：driver / risk / focus_point
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 目标标识，定位到 node_state 中的具体条目
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # 生效时间
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 失效时间，NULL 表示永久有效
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 续期次数
    extended_count: Mapped[int] = mapped_column(Integer, default=0)
    # 失效原因
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 支撑失效判断的证据 ID
    invalidation_evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("ix_validity_target", "target_type", "target_id"),
        Index("ix_validity_until", "valid_until"),
    )
