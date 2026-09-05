from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema, BeijingTime


class TradingOperationCreate(BaseSchema):
    """交易操作创建请求 —— 功能11"""
    operation_type: str  # 操作类型：buy / sell / skip / track / stop_loss / take_profit
    target_node_id: UUID | None = None  # 操作标的对应的世界节点 ID
    trigger_analysis_id: UUID | None = None  # 触发该操作的分析 ID
    parent_operation_id: UUID | None = None  # 关联的父交易操作 ID（自引用）
    trigger_raw_ids: list[UUID] | None = None  # 触发该操作的原始证据资讯 ID 列表
    symbol: str | None = None  # 交易代码
    quantity: float | None = None  # 交易数量
    price: float | None = None  # 成交价格
    rationale: str | None = None  # 操作理由
    expected_impact: str | None = None  # 预期影响
    risk_level: str | None = None  # 风险等级：low / medium / high / critical
    status: str = "pending"  # 操作状态：pending / executed / cancelled / expired
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class TradingOperationUpdate(BaseSchema):
    """交易状态更新请求"""
    status: str  # approved | rejected
    reason: str | None = None  # 拒绝原因（rejected 时必填）
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class TradingOperationResponse(BaseSchema):
    """交易操作出参"""
    id: UUID  # 交易记录 ID
    operation_type: str  # 操作类型
    target_node_id: UUID | None  # 目标节点 ID
    trigger_analysis_id: UUID | None  # 触发分析 ID
    parent_operation_id: UUID | None  # 父交易操作 ID（自引用）
    trigger_raw_ids: list[UUID] | None  # 原始证据 ID 列表
    symbol: str | None  # 交易代码
    quantity: float | None  # 交易数量
    price: float | None  # 成交价格
    rationale: str | None  # 操作理由
    expected_impact: str | None  # 预期影响
    risk_level: str | None  # 风险等级
    status: str  # 操作状态
    executed_at: datetime | None  # 实际执行时间
    created_at: datetime  # 创建时间
