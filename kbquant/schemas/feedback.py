from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema, BeijingTime


class FeedbackCreate(BaseSchema):
    """反馈/复盘的创建请求 —— 功能12"""
    title: str  # 复盘标题
    trigger_analysis_id: UUID | None = None  # 触发交易的分析 ID
    trigger_trade_id: UUID | None = None  # 被复盘交易 ID
    expected_outcome: str | None = None  # 当时预期的结果
    actual_outcome: str | None = None  # 实际发生的结果
    judgment_correct: bool | None = None  # 方向判断是否正确
    error_reason: str | None = None  # 错误原因分析
    missed_factors: str | None = None  # 遗漏的因素
    adjustment_suggestions: str | None = None  # 后续调整建议
    market_environment_snapshot: dict | None = None  # 复盘时的市场环境快照，含指数、情绪、关键数据
    lessons_learned: str | None = None  # 提炼的经验教训
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class FeedbackResponse(BaseSchema):
    """反馈/复盘出参"""
    id: UUID  # 复盘记录 ID
    title: str  # 复盘标题
    trigger_analysis_id: UUID | None  # 触发分析 ID
    trigger_trade_id: UUID | None  # 触发交易 ID
    expected_outcome: str | None  # 预期结果
    actual_outcome: str | None  # 实际结果
    judgment_correct: bool | None  # 判断是否正确
    error_reason: str | None  # 错误原因
    missed_factors: str | None  # 遗漏因素
    adjustment_suggestions: str | None  # 调整建议
    market_environment_snapshot: dict | None  # 市场环境快照
    lessons_learned: str | None  # 经验教训
    created_at: datetime  # 创建时间
