from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema, BeijingTime


class NodeStateCreate(BaseSchema):
    """节点状态更新请求 —— 功能6"""
    core_logic: str | None = None  # 核心投资逻辑/主逻辑
    primary_drivers: list[dict] | None = None  # 主要驱动因素 [{driver, strength, evidence_ids, valid_until}]
    risks: list[dict] | None = None  # 风险列表 [{risk, severity, evidence_ids, valid_until}]
    focus_points: list[dict] | None = None  # 关注点 [{point, priority, evidence_ids}]
    recent_changes: str | None = None  # 最近变化摘要
    uncertainty_flags: list[str] | None = None  # 标记为不确定的区域
    key_evidence_ids: list[UUID] | None = None  # 支撑该状态的关键证据 ID 列表
    state_summary: str | None = None  # Agent 用的压缩摘要（控制上下文长度）
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class WorldNodeCreate(BaseSchema):
    """世界节点创建请求 —— 功能4"""
    name: str  # 节点名称，如 "贵州茅台"、"白酒板块"、"货币政策"
    node_type: str  # 节点类型：company / sector / macro_theme / concept / product / policy / institution / region / person
    description: str | None = None  # 简要描述
    ticker: str | None = None  # 股票代码，仅公司节点使用
    aliases: list[str] | None = None  # 别名列表，用于实体匹配
    metadata_: dict | None = None  # 扩展属性
    initial_state: NodeStateCreate | None = None  # 可选的初始状态，创建节点时一步到位设置投资逻辑/驱动因素/风险等
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class WorldNodeResponse(BaseSchema):
    """世界节点出参"""
    id: UUID  # 节点唯一标识
    name: str  # 节点名称
    node_type: str  # 节点类型
    description: str | None  # 描述
    ticker: str | None  # 股票代码
    aliases: list[str] | None  # 别名
    metadata_: dict | None  # 扩展属性
    is_active: bool  # 是否激活（软删除标记）
    created_at: datetime  # 创建时间


class NodeStateResponse(BaseSchema):
    """节点状态出参 —— 功能5"""
    id: UUID  # 状态记录 ID
    node_id: UUID  # 所属节点 ID
    version: int  # 版本号，每次更新单调递增
    effective_from: datetime  # 该版本生效时间
    effective_to: datetime | None  # 该版本失效时间，NULL 表示当前版本
    core_logic: str | None  # 核心投资逻辑
    primary_drivers: list[dict] | None  # 主要驱动因素
    risks: list[dict] | None  # 风险列表
    focus_points: list[dict] | None  # 关注点
    recent_changes: str | None  # 最近变化
    uncertainty_flags: list[str] | None  # 不确定区域
    key_evidence_ids: list[UUID] | None  # 关键证据 ID
    state_summary: str | None  # 压缩摘要
    created_at: datetime  # 创建时间


class NodeAttachmentCreate(BaseSchema):
    """节点挂载请求 —— 功能4"""
    attachment_type: str  # 挂载类型：raw_info 或 analysis
    attachment_id: UUID  # 挂载对象 ID（资讯 ID 或分析 ID）
    role: str  # 挂载角色：primary / secondary / background / risk / historical_reference / driver_evidence / risk_evidence
    relevance_score: float | None = None  # 相关性评分 0.0~1.0
    custom_time: BeijingTime | None = None  # 自定义写入时间（北京时间），用于回溯/导入历史数据。不传则使用服务器当前时间


class NodeAttachmentResponse(BaseSchema):
    """节点挂载出参"""
    id: UUID  # 挂载记录 ID
    node_id: UUID  # 所属节点 ID
    attachment_type: str  # 挂载类型
    attachment_id: UUID  # 挂载对象 ID
    role: str  # 挂载角色
    relevance_score: float | None  # 相关性评分
    created_at: datetime  # 创建时间


class NodeCompressionRequest(BaseSchema):
    """节点摘要压缩请求 —— 功能19"""
    force: bool = False  # 是否强制压缩（即使证据量未达到阈值）
    target_compression_ratio: float = 0.3  # 目标压缩比，0.3 表示压缩到原始大小的 30%


class NodeCompressionResponse(BaseSchema):
    """节点摘要压缩结果 —— 功能19"""
    node_id: UUID  # 被压缩的节点 ID
    name: str  # 节点名称
    before_evidence_count: int  # 压缩前证据条数
    before_total_chars: int  # 压缩前总字符数
    after_evidence_count: int  # 压缩后证据条数
    after_total_chars: int  # 压缩后总字符数
    new_state_id: UUID | None = None  # 新创建的状态 ID（skip 时为 None）
    summary: str  # 压缩后的摘要文本
    skipped: bool = False  # 是否因未达阈值而跳过

class NodeNameAliases(BaseSchema):
    """节点名称和别名"""
    id: UUID
    name: str
    node_type: str
    aliases: list[str] | None = None


class WorldNodeEdgeCreate(BaseSchema):
    """创建节点关联边

    relationship_type 使用 WorldNodeEdgeType 枚举值：
      belongs_to, classified_as, operates_in, has_business_segment,
      derives_revenue_from, upstream_of, downstream_of, competes_in,
      threatens, regulated_by, benefits_from, constrained_by,
      affected_by, driven_by, based_in, exposed_to, led_by,
      affiliated_with
    """
    parent_node_id: UUID
    child_node_id: UUID
    relationship_type: str = "belongs_to"
    weight: float = 1.0
    evidence_ids: list[UUID] | None = None


class WorldNodeEdgeResponse(BaseSchema):
    """节点关联边出参"""
    id: UUID
    parent_node_id: UUID
    child_node_id: UUID
    relationship_type: str
    weight: float
    evidence_ids: list[UUID] | None
    created_at: datetime

