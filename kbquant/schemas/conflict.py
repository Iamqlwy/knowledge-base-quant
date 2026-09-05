from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema


class ConflictDetectRequest(BaseSchema):
    """冲突检测请求 —— 功能15"""
    node_id: UUID  # 受影响的节点 ID
    existing_claim: str  # 已有状态中的主张
    conflicting_claim: str  # 新资讯中的矛盾主张
    existing_evidence_id: UUID | None = None  # 已有主张的证据 ID
    conflicting_evidence_id: UUID | None = None  # 新主张的证据 ID
    conflict_type: str = "contradiction"  # 冲突类型：contradiction(矛盾) / refinement(细化) / negation(否定) / update(更新)


class ConflictDetectResponse(BaseSchema):
    """冲突检测结果"""
    has_conflict: bool  # 是否存在冲突
    conflict_type: str | None = None  # 冲突类型
    conflict_id: UUID | None = None  # 冲突记录 ID
    similar_conflicts: list[dict] | None = None  # 历史相似冲突


class ConflictResolveRequest(BaseSchema):
    """冲突解决请求 —— 功能15"""
    resolution: str  # 解决方案描述


class ConflictResponse(BaseSchema):
    """冲突记录出参"""
    id: UUID  # 冲突记录 ID
    node_id: UUID  # 受影响节点 ID
    existing_claim: str  # 已有主张
    conflicting_claim: str  # 矛盾主张
    existing_evidence_id: UUID | None  # 已有主张证据 ID
    conflicting_evidence_id: UUID | None  # 矛盾主张证据 ID
    conflict_type: str  # 冲突类型
    resolution: str | None  # 解决方案
    resolved_at: datetime | None  # 解决时间，NULL 表示未解决
    created_at: datetime  # 创建时间
