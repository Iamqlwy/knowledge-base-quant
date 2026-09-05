from datetime import datetime
from uuid import UUID

from pydantic import Field

from kbquant.schemas import BaseSchema, BeijingTime


class RawInformationCreate(BaseSchema):
    """原始资讯入库请求 —— 功能1"""
    title: str  # 资讯标题/摘要
    body: str  # 资讯正文
    source: str  # 来源，如 央行官网 / Reuters / Twitter
    source_url: str | None = None  # 原始 URL
    published_at: BeijingTime  # 资讯发布时间（北京时间，自动转为UTC存储）
    info_type: str  # 资讯类型：news / report / social_media / filing / research / other
    language: str = "zh"  # 语言代码
    raw_metadata: dict | None = None  # 扩展元数据，存储来源特有的非结构化字段


class RawInformationResponse(BaseSchema):
    """原始资讯出参"""
    id: UUID  # 系统分配的唯一标识
    title: str  # 资讯标题
    body: str  # 资讯正文
    source: str  # 来源
    source_url: str | None  # 原始 URL
    published_at: datetime  # 资讯发布时间
    ingested_at: datetime  # 系统入库时间
    info_type: str  # 资讯类型
    language: str  # 语言代码
    raw_metadata: dict | None  # 扩展元数据
    content_hash: str  # SHA-256 (标题+正文)，用于去重
    processing_status: str  # 处理流水线状态：ingested → deduped → entities_extracted → ...
    importance_score: float  # 重要性评分 0.0~1.0
    created_at: datetime  # 记录创建时间
    updated_at: datetime  # 记录更新时间


class DedupCheckRequest(BaseSchema):
    """去重检查请求 —— 功能2"""
    title: str  # 待检查的资讯标题
    body: str  # 待检查的资讯正文
    info_type: str | None = None  # 资讯类型过滤
    source: str | None = None  # 来源过滤
    threshold: float = 0.85  # 向量相似度阈值，超过即视为重复


class DedupMatch(BaseSchema):
    """匹配到的重复资讯"""
    id: UUID  # 已存在的资讯 ID
    title: str  # 已存在资讯的标题
    similarity_score: float  # 相似度分数


class DedupCheckResponse(BaseSchema):
    """去重检查结果"""
    is_duplicate: bool  # 是否为重复资讯
    primary_id: UUID | None = None  # 主资讯 ID（原始版本）
    similarity_score: float | None = None  # 最高相似度
    matches: list[DedupMatch] = []  # 所有匹配项列表


class InformationMergeRequest(BaseSchema):
    """合并请求 —— 功能2"""
    primary_id: UUID  # 主资讯 ID（保留的版本）
    duplicate_id: UUID  # 重复资讯 ID（被合并的版本）
    dedup_type: str  # 去重类型：exact_duplicate / reprint / follow_up / same_event / superseded
    dedup_rationale: str | None = None  # 去重理由说明


class InformationMergeResponse(BaseSchema):
    """合并结果"""
    id: UUID  # 去重记录 ID
    primary_id: UUID = Field(validation_alias="primary_info_id")  # 主资讯 ID
    duplicate_id: UUID = Field(validation_alias="duplicate_info_id")  # 被合并的资讯 ID
    dedup_type: str  # 去重类型


class BatchUpdateImportanceRequest(BaseSchema):
    """批量更新重要性评分请求"""
    scores: dict[UUID, float]  # {info_id: importance_score}


class BatchUpdateImportanceResponse(BaseSchema):
    """批量更新重要性评分响应"""
    updated: int  # 成功更新的行数
