from datetime import datetime
from typing import Literal

from kbquant.schemas import BaseSchema


SearchMode = Literal["hybrid", "embedding", "bm25"]


class DateRange(BaseSchema):
    """时间范围过滤（ISO 8601 字符串或 datetime）

    start / end 均为可选，单独指定一端亦可。
    示例: {"start": "2025-01-01", "end": "2026-06-12"}
    """
    start: str | datetime | None = None
    end: str | datetime | None = None


class SearchRequest(BaseSchema):
    """统一搜索请求

    mode:
        hybrid   - 混合搜索（BM25 + 向量 + 名称匹配 + 结构权重 + 时间衰减）
        embedding - 纯向量语义搜索
        bm25     - 纯 BM25 关键词搜索
    date_range:
        可选。对 raw_information / analyses / feedbacks 按 published_at 做时间范围过滤。
    only_tables:
        可选。限定只搜索指定表，跳过阶段3的表决策。
        合法值: raw_information, analyses, nodes, feedbacks
    """
    query_text: str
    mode: SearchMode = "hybrid"
    filters: dict | None = None
    weights: dict | None = None
    limit: int = 20
    include_explanations: bool = False
    date_range: DateRange | None = None
    only_tables: list[str] | None = None


class SearchScoreBreakdown(BaseSchema):
    """搜索评分明细"""
    total: float
    bm25_rank: int | None = None
    vector_rank: int | None = None
    structural: float | None = None
    time_score: float | None = None
    reranker: float | None = None


class SearchResult(BaseSchema):
    """搜索单条结果"""
    result_type: str
    id: str
    title: str
    snippet: str | None = None
    time: datetime | None = None
    score: SearchScoreBreakdown


class SearchResponse(BaseSchema):
    """搜索响应"""
    items: list[SearchResult]
    total: int


class FetchByIdsRequest(BaseSchema):
    table_ids: dict[str, list[str]]


class FetchByIdsResponse(BaseSchema):
    data: dict[str, list[dict]]
