import uuid

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.models.base import BaseModel


class InformationDedup(BaseModel):
    """去重记录 —— 追踪重复/转载/同事件/后续报道之间的关系"""
    __tablename__ = "information_dedups"

    # 主资讯 ID（保留的权威版本）
    primary_info_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 重复资讯 ID（被合并/标记为重复的版本）
    duplicate_info_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # 去重类型：exact_duplicate(完全重复) / reprint(转载) / follow_up(后续报道) / same_event(同一事件) / superseded(已过时)
    dedup_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # 相似度分数（向量相似度或 hash 匹配度）
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 去重理由说明
    dedup_rationale: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        # (主, 副) 组合唯一，同一对不重复记录
        Index("ix_dedup_unique", "primary_info_id", "duplicate_info_id", unique=True),
    )
