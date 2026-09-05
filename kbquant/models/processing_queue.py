import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class ProcessingQueue(BaseModel):
    """处理队列 —— 跟踪每条资讯的处理管线状态和进度"""
    __tablename__ = "processing_queue"

    # 对应的原始资讯，一对一
    raw_info_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    # 当前处理状态：ingested → deduped → entities_extracted → attached_to_nodes → analyzed → world_model_updated → trade_validated → completed
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ingested", index=True)
    preprocess_status: Mapped[str] = mapped_column(String(50), nullable=False, default="ingested", index=True)
    # 状态变更历史: [{status, timestamp, detail}]
    status_history: Mapped[list | None] = mapped_column(JSONB, default=list)
    # 优先级，数值越大越优先处理
    priority: Mapped[int] = mapped_column(Integer, default=0)
    # 最近错误信息，仅在 error 状态时填写
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 当前负责处理的 Agent 标识
    agent_assigned: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # 开始处理时间
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 完成时间
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_processing_queue_preprocess_priority_created", "preprocess_status", "priority", "created_at"),
        Index("ix_processing_queue_status_priority", "status", "priority"),
    )
