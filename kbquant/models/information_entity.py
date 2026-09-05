import uuid

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class InformationEntity(BaseModel):
    """资讯-实体关联（多对多中间表）—— 记录每条资讯中出现了哪些实体及其角色"""
    __tablename__ = "information_entities"

    # 资讯 ID
    raw_info_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 实体 ID
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # 实体在资讯中的角色：subject(主语) / object(宾语) / context(背景) / mentioned(提及)
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 相关性评分 0.0~1.0
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 实体提取置信度 0.0~1.0
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # 同一资讯同一实体只关联一次
        Index("ix_info_entity_unique", "raw_info_id", "entity_id", unique=True),
    )
