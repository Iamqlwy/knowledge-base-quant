import uuid

from sqlalchemy import Float, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class EntityRelationship(BaseModel):
    """实体关系 —— 知识图谱 Layer 2 的边，描述实体间的供应链/竞争/关联等关系"""
    __tablename__ = "entity_relationships"

    # 源实体
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 目标实体
    target_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 关系类型：见 RelationshipType 枚举（impacts / regulates / sanctions / holds / part_of / supplies / competes_with / substitutes / correlated_with / same_as）
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 关系强度 0.0~1.0
    strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 支撑该关系的证据 ID 列表
    evidence_info_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关系首次观察到的时间
    valid_from: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 关系失效时间，NULL 表示仍有效
    valid_until: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        # 按源实体+关系类型查询：找出 A 的所有下游
        Index("ix_er_source_type", "source_entity_id", "relationship_type"),
        # 按目标实体+关系类型查询：找出谁在供应 B
        Index("ix_er_target_type", "target_entity_id", "relationship_type"),
    )
