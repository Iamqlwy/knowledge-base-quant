import uuid

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class NodeAttachment(BaseModel):
    """节点挂载 —— 将原始资讯或分析关联到世界节点，并标记角色"""
    __tablename__ = "node_attachments"

    # 目标节点
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 挂载对象类型：raw_info 或 analysis
    attachment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 挂载对象 ID（多态 FK，由 attachment_type 决定指向哪个表）
    attachment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 挂载角色：primary / secondary / background / risk / historical_reference / driver_evidence / risk_evidence
    role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 相关性评分 0.0~1.0
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # 按节点+角色查询：某节点的所有 primary 证据
        Index("ix_node_attachments_node_role", "node_id", "role"),
        # 反向查找：某条资讯/分析被挂载到了哪些节点
        Index("ix_node_attachments_ref", "attachment_type", "attachment_id"),
    )
