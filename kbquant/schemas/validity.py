from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema


class TimeValidityCreate(BaseSchema):
    """时效管理创建请求 —— 功能14"""
    target_type: str  # 目标类型：driver / risk / focus_point
    target_id: str  # 目标标识，用于定位到具体的 driver/risk/focus_point
    valid_from: datetime  # 生效时间
    valid_until: datetime | None = None  # 失效时间，NULL 表示永久有效


class TimeValidityResponse(BaseSchema):
    """时效管理出参"""
    id: UUID  # 时效记录 ID
    target_type: str  # 目标类型
    target_id: str  # 目标标识
    valid_from: datetime  # 生效时间
    valid_until: datetime | None  # 失效时间
    extended_count: int  # 续期次数
    invalidation_reason: str | None  # 失效原因（人工标记失效时填写）
    invalidation_evidence_id: UUID | None  # 支撑失效判断的证据 ID
    created_at: datetime  # 创建时间


class ValidityExpireRequest(BaseSchema):
    """标记失效请求 —— 功能14"""
    invalidation_reason: str | None = None  # 失效原因
    invalidation_evidence_id: UUID | None = None  # 支撑证据 ID


class ValidityExtendRequest(BaseSchema):
    """续期请求 —— 功能14"""
    new_valid_until: datetime  # 新的失效时间


class ValidityCheckResponse(BaseSchema):
    """时效检查结果 —— 功能14"""
    is_valid: bool  # 是否仍然有效
    validity_entry: TimeValidityResponse | None = None  # 时效记录（若存在）
    reason: str | None = None  # 无效时的原因
