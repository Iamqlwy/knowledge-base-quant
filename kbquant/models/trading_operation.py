import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class TradingOperation(BaseModel):
    """交易操作 —— 记录每一次买入/卖出/跳过/跟踪/止损/止盈动作"""
    __tablename__ = "trading_operations"

    # 操作类型：buy / sell / skip / track / stop_loss / take_profit
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 操作标的对应的世界节点
    target_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # 触发该操作的依据分析
    trigger_analysis_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    parent_operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # 触发该操作的原始证据资讯 ID 列表
    trigger_raw_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    # 交易代码/标的符号
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 交易数量
    quantity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # 成交价格
    price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # 操作理由
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 预期影响
    expected_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 风险等级：low / medium / high / critical
    risk_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 操作状态：pending / executed / cancelled / expired
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    # 实际执行时间
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
