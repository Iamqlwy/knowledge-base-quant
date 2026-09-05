from datetime import datetime
from uuid import UUID

from kbquant.schemas import BaseSchema


class EvidenceItem(BaseSchema):
    """证据链中的单条证据"""
    type: str  # 证据类型：raw_info / analysis / node_state
    id: UUID  # 证据 ID
    title: str  # 证据标题
    summary: str | None  # 证据摘要（截断至前200字符）
    timestamp: datetime  # 证据时间戳


class EvidenceChain(BaseSchema):
    """证据链的一层"""
    level: int  # 回溯层级：0=直接证据，1=一级间接，2=二级间接
    items: list[EvidenceItem]  # 该层的证据项


class EvidenceTraceResponse(BaseSchema):
    """证据回溯结果 —— 功能8"""
    root: dict  # 回溯起点：{type, id}
    evidence_chain: list[EvidenceChain]  # 证据链，按层级递进
