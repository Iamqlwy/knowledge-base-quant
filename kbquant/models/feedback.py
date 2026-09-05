import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.config import settings
from kbquant.models.base import BaseModel


class Feedback(BaseModel):
    """反馈/复盘 —— 交易或观察结束后的回顾总结，用于持续优化决策质量"""
    __tablename__ = "feedbacks"

    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 触发该复盘的分析 ID
    trigger_analysis_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # 被复盘的交易 ID
    trigger_trade_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # 当时预期的结果
    expected_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 实际发生的结果
    actual_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 方向判断是否正确（不考虑幅度）
    judgment_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    # 错误根因分析
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 遗漏的因素
    missed_factors: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 后续流程调整建议
    adjustment_suggestions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 复盘时的市场环境快照（指数、情绪、关键数据等）
    market_environment_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 提炼的经验教训，可供后续相似案例搜索复用
    lessons_learned: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dimension), nullable=True)

    __table_args__ = ()
