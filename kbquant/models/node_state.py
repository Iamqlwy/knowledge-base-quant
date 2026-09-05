import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.config import settings
from kbquant.models.base import BaseModel


class NodeState(BaseModel):
    """节点状态 —— 版本化的节点快照，是 as-of-time 查询和证据回溯的核心"""
    __tablename__ = "node_states"

    # 所属节点
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # 版本号，每次更新单调递增，同一节点下唯一
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 该版本生效时间
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 该版本失效时间，NULL 表示当前有效版本。更新时先关闭旧版本（设 effective_to=now），再创建新版本
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 核心投资逻辑/主逻辑
    core_logic: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 主要驱动因素: [{driver, strength, evidence_ids, valid_until}]
    primary_drivers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 风险列表: [{risk, severity, evidence_ids, valid_until}]
    risks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 关注点: [{point, priority, evidence_ids}]
    focus_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 与上一版本相比的变化摘要
    recent_changes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 标记为不确定的区域
    uncertainty_flags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 支撑该状态的关键证据 ID 列表（同时包含 raw_info 和 analysis）
    key_evidence_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    # Agent 用的压缩摘要，控制上下文窗口长度
    state_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dimension), nullable=True)

    __table_args__ = (
        # node_id + version 联合唯一，保证版本链不重复
        Index("ix_node_states_version", "node_id", "version", unique=True),
        # 加速"查询当前状态"：WHERE node_id = X AND effective_to IS NULL
        Index("ix_node_states_current", "node_id", "effective_to"),
        # GIN 索引加速 key_evidence_ids 的数组包含查询
        Index("ix_node_states_evidence", "key_evidence_ids", postgresql_using="gin"),
    )
