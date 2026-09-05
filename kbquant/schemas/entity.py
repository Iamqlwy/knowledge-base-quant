from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema


class EntityCreate(BaseSchema):
    """实体创建请求 —— 功能3"""
    name: str  # 实体名称，如 "贵州茅台"、"600519.SH"、"白酒"
    entity_type: str  # 实体类型：company / stock_code / sector / concept / product / policy / institution / region / person / upstream / downstream
    aliases: list[str] | None = None  # 别名/简称列表
    metadata_: dict | None = None  # 提取时的上下文信息
    linked_node_id: UUID | None = None  # 关联的世界节点 ID


class EntityResponse(BaseSchema):
    """实体出参"""
    id: UUID  # 实体唯一标识
    name: str  # 实体名称
    entity_type: str  # 实体类型
    normalized_name: str  # 标准化名称（大小写/空白统一后），用于精确匹配
    aliases: list[str] | None  # 别名列表
    metadata_: dict | None  # 提取上下文
    linked_node_id: UUID | None  # 关联的世界节点 ID
    created_at: datetime  # 创建时间


class EntityExtractRequest(BaseSchema):
    """实体提取请求 —— 功能3"""
    entities: list[dict]  # 提取到的实体列表 [{name, entity_type, role?, relevance_score?, extraction_confidence?}]


class InformationEntityResponse(BaseSchema):
    """资讯-实体关联出参"""
    id: UUID  # 关联记录 ID
    raw_info_id: UUID  # 资讯 ID
    entity_id: UUID  # 实体 ID
    role: str | None  # 实体在资讯中的角色：subject(主语) / object(宾语) / context(背景) / mentioned(提及)
    relevance_score: float | None  # 相关性评分
    extraction_confidence: float | None  # 提取置信度


class EntityRelationshipCreate(BaseSchema):
    """实体关系创建请求 —— 功能7"""
    source_entity_id: UUID  # 源实体 ID
    target_entity_id: UUID  # 目标实体 ID
    relationship_type: str  # 关系类型：见 RelationshipType 枚举
    strength: float | None = None  # 关系强度 0.0~1.0
    evidence_info_ids: list[UUID] | None = None  # 支持该关系的证据 ID 列表
    description: str | None = None  # 关系说明


class EntityRelationshipResponse(BaseSchema):
    """实体关系出参"""
    id: UUID  # 关系记录 ID
    source_entity_id: UUID  # 源实体 ID
    target_entity_id: UUID  # 目标实体 ID
    relationship_type: str  # 关系类型
    strength: float | None  # 关系强度
    evidence_info_ids: list[UUID] | None  # 证据 ID 列表
    description: str | None  # 关系说明
    valid_from: str | None  # 关系首次观察到的时间
    valid_until: str | None  # 关系失效时间，NULL 表示仍有效
    created_at: datetime  # 创建时间


class ImpactPathResponse(BaseSchema):
    """影响路径搜索结果 —— 功能7"""
    root: EntityResponse  # 起点实体
    paths: list[dict]  # 影响路径列表 [{path: [{entity_id, entity_name, relationship_type}], total_impact_strength}]
