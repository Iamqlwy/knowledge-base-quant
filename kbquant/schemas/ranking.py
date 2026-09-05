from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema


class RankingComputeRequest(BaseSchema):
    """重要性排序计算请求 —— 功能16"""
    target_type: str  # 排序目标类型：raw_info / node / analysis / trade_candidate
    target_id: UUID  # 目标 ID
    force_recompute: bool = False  # 是否强制重新计算


class RankingComputeResponse(BaseSchema):
    """重要性排序计算结果"""
    score: float  # 综合重要性得分 0.0~1.0
    components: dict | None = None  # 各子维度得分：recency / centrality / source_authority / agent_demand / unresolved
    rank_position: int | None = None  # 同类排名


class RankingHistoryResponse(BaseSchema):
    """排序历史出参"""
    id: UUID  # 记录 ID
    target_type: str  # 目标类型
    target_id: UUID  # 目标 ID
    importance_score: float  # 重要性得分
    score_components: dict | None  # 得分明细
    rank_position: int | None  # 同类排名
    computed_at: datetime  # 计算时间
