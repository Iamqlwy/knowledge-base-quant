import uuid

from sqlalchemy import Boolean, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class WorldNode(BaseModel):
    """世界节点 —— 描述公司/板块/宏观等事物的当前状态，知识图谱 Layer 1"""
    __tablename__ = "world_nodes"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        Index("ix_world_nodes_name_type", "name", "node_type", unique=True),
        Index("ix_world_nodes_aliases", "aliases", postgresql_using="gin"),
        Index("ix_world_nodes_active_type_name", "is_active", "node_type", "name"),
    )


class WorldNodeEdge(BaseModel):
    """世界节点多对多关联表 —— 知识图谱层级关系边"""
    __tablename__ = "world_node_edges"

    parent_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    child_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False, default="belongs_to")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)

    __table_args__ = (
        Index("ix_wne_unique_edge", "parent_node_id", "child_node_id", "relationship_type", unique=True),
    )
