import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.database import Base


class BaseModel(Base):
    """所有模型的抽象基类，提供 UUID 主键和自动时间戳"""
    __abstract__ = True

    # UUID 主键，数据库自动生成，避免自增 ID 的安全和分布式问题
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 记录创建时间，由服务端自动设置
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    # 记录更新时间，每次 UPDATE 时自动刷新
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
