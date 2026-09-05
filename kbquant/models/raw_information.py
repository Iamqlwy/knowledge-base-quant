from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from kbquant.config import settings
from kbquant.models.base import BaseModel


class RawInformation(BaseModel):
    """原始资讯 —— 知识库的数据源头，所有分析和决策的证据基础"""
    __tablename__ = "raw_information"

    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # 来源名称，如 "央行官网" / "Reuters" / "Twitter"
    source: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # 原始 URL，用于证据回溯时提供可访问的链接
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 资讯发布时间（原始发布时间，非系统入库时间）
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # 系统入库时间，用于 as-of-time 查询防止未来信息泄露
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    # 资讯类型：news / report / social_media / filing / research / other
    info_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(10), default="zh")
    # 来源特有的非结构化字段
    raw_metadata: Mapped[dict | None] = mapped_column("raw_metadata", JSONB, default=dict)
    # SHA-256(标准化标题+正文)，用于 O(1) 精确去重
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 处理管线状态：ingested → deduped → entities_extracted → attached_to_nodes → analyzed → world_model_updated → trade_validated → completed
    processing_status: Mapped[str] = mapped_column(String(50), default="ingested", index=True)
    # 重要性评分 0.0~1.0，由 ranking_service 计算
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    # 向量嵌入，用于语义相似度搜索和近重复检测
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dimension), nullable=True)

    __table_args__ = (
        # content_hash 唯一索引：同内容不重复入库
        Index("ix_raw_info_content_hash", "content_hash", unique=True),
        Index("ix_raw_info_importance", "importance_score"),
    )
