import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class ConflictDetection(BaseModel):
    """冲突检测 —— 记录新资讯与已有状态之间的矛盾，由 Agent 明确提交"""
    __tablename__ = "conflict_detections"

    # 受影响的节点
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # 已有状态中的主张
    existing_claim: Mapped[str] = mapped_column(Text, nullable=False)
    # 新资讯中的矛盾主张
    conflicting_claim: Mapped[str] = mapped_column(Text, nullable=False)
    # 已有主张的证据 ID
    existing_evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 矛盾主张的证据 ID
    conflicting_evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 冲突类型：contradiction(矛盾) / refinement(细化) / negation(否定) / update(更新)
    conflict_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 解决方案描述
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 解决时间，NULL 表示未解决
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 加速查询未解决的冲突
        Index("ix_conflicts_unresolved", "resolved_at"),
    )
