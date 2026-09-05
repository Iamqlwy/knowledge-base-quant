import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class ImportanceRanking(BaseModel):
    """重要性排名 —— 对资讯/节点/分析/交易候选进行优先级排序，帮助 Agent 决定处理顺序"""
    __tablename__ = "importance_rankings"

    # 排序目标类型：raw_info / node / analysis / trade_candidate
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 目标 ID
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 综合重要性得分 0.0~1.0
    importance_score: Mapped[float] = mapped_column(Float, nullable=False)
    # 各子维度得分明细：recency / centrality / source_authority / agent_demand / unresolved
    score_components: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 同类排名
    rank_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 计算时间，每次重算产生新记录以支持趋势分析
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # 按目标+时间查询排名历史
        Index("ix_ranking_target_score", "target_type", "target_id", "computed_at"),
        # 按分数排序查询 Top N
        Index("ix_ranking_score", "importance_score"),
    )
