import uuid

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class Entity(BaseModel):
    """实体 —— 从资讯中抽取的公司/人物/产品/政策等，知识图谱 Layer 2 的节点"""
    __tablename__ = "entities"

    # 实体名称，如 "贵州茅台" / "600519.SH" / "白酒"
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 实体类型：company / stock_code / sector / concept / product / policy / institution / region / person / upstream / downstream
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 标准化名称（小写+去空白），用于精确匹配和去重
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 别名/简称列表
    aliases: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)), nullable=True)
    # 提取时的上下文信息
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # 关联的世界节点 ID，将 Layer 2（实体图）桥接到 Layer 1（节点树）
    linked_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    __table_args__ = (
        # 标准化名称+类型联合唯一，同一实体不重复创建
        Index("ix_entities_norm_name_type", "normalized_name", "entity_type", unique=True),
        Index("ix_entities_aliases", "aliases", postgresql_using="gin"),
    )
