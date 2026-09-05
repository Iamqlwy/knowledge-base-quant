from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema, BeijingTime, BeijingTime


class AnalysisCreate(BaseSchema):
    """分析信息创建请求 —— 功能10"""
    title: str  # 分析标题
    content: str  # 分析正文
    analysis_type: str  # 分析类型：impact_analysis / driver_assessment / risk_evaluation / sentiment / valuation / technical / macro
    agent_id: str | None = None  # 执行分析的 Agent 标识
    confidence: float | None = None  # 置信度 0.0~1.0
    parent_analysis_id: UUID | None = None  # 父分析 ID，用于链式分析
    root_raw_info_ids: list[UUID] | None = None  # 触发该分析的原始资讯 ID 列表
    time_horizon: str | None = None  # 分析时效：short_term / medium_term / long_term
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class AnalysisResponse(BaseSchema):
    """分析信息出参"""
    id: UUID  # 分析唯一标识
    title: str  # 分析标题
    content: str  # 分析正文
    analysis_type: str  # 分析类型
    agent_id: str | None  # 执行 Agent
    confidence: float | None  # 置信度
    parent_analysis_id: UUID | None  # 父分析 ID
    root_raw_info_ids: list[UUID] | None  # 原始资讯 ID 列表
    time_horizon: str | None  # 分析时效
    created_at: datetime  # 创建时间
    updated_at: datetime  # 更新时间
