import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.config import settings
from kbquant.models.base import BaseModel


class Analysis(BaseModel):
    """分析信息 —— Agent 对原始资讯的加工分析，是交易决策的直接依据"""
    __tablename__ = "analyses"

    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 分析类型：impact_analysis / driver_assessment / risk_evaluation / sentiment / valuation / technical / macro
    analysis_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 执行分析的 Agent 标识
    agent_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # 置信度 0.0~1.0
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 父分析 ID，支持链式/迭代分析
    parent_analysis_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 触发该分析的原始资讯 ID 列表，用于证据回溯
    root_raw_info_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    # 分析时效：short_term / medium_term / long_term
    time_horizon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dimension), nullable=True)

    __table_args__ = (
        Index("ix_analyses_confidence", "confidence"),
        Index("ix_analyses_root_raw_info", "root_raw_info_ids", postgresql_using="gin"),
    )
