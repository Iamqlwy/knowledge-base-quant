from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema


class PipelineStatusUpdate(BaseSchema):
    """处理流水线状态更新请求 —— 功能13"""
    status: str  # 新状态：ingested / deduped / entities_extracted / attached_to_nodes / analyzed / world_model_updated / trade_validated / completed / error
    detail: str | None = None  # 状态变更说明
    priority: int | None = None  # 新的优先级，数值越大越优先


class PipelineQueueEntry(BaseSchema):
    """处理队列条目出参"""
    id: UUID  # 队列记录 ID
    raw_info_id: UUID  # 对应的原始资讯 ID
    status: str  # 当前处理状态
    status_history: list | None  # 状态变更历史 [{status, timestamp, detail}]
    priority: int  # 优先级
    last_error: str | None  # 最近的错误信息（仅 error 状态）
    agent_assigned: str | None  # 当前负责处理的 Agent
    started_at: datetime | None  # 开始处理时间
    completed_at: datetime | None  # 完成时间
    created_at: datetime  # 创建时间


class PipelineStats(BaseSchema):
    """处理流水线统计 —— 功能13"""
    by_status: dict[str, int]  # 按状态统计数量
    total_pending: int  # 待处理总数（除 completed 和 error 外）
    avg_processing_time: float | None  # 平均处理耗时（秒）


class ReprioritizeRequest(BaseSchema):
    """批量调整优先级请求"""
    item_ids: list[UUID]  # 要调整的队列条目 ID 列表
    new_priority: int  # 目标优先级
