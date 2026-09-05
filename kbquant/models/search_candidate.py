"""Search pipeline data models — lightweight dataclasses for intraservice use.

These are NOT SQLAlchemy models. They carry data between pipeline stages.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class EntityResult:
    """A single recognized entity from the query."""
    name: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)
    ticker: str | None = None
    match_method: str = ""  # ticker_exact, name_exact, alias_exact, contains
    score: float = 0.0
    node_id: str | None = None  # WorldNode UUID for cross-channel fusion


@dataclass
class Candidate:
    """A single search candidate before final ranking."""
    id: str
    table_name: str  # raw_information, analyses, feedbacks, nodes
    result_type: str  # raw_information, analysis, feedback, node
    title: str = ""
    snippet: str = ""
    time: datetime | None = None

    # Per-channel scores
    bm25_score: float = 0.0
    vector_score: float = 0.0
    name_match_score: float = 0.0
    rrf_score: float = 0.0
    reranker_score: float = 0.0
    final_score: float = 0.0

    # Structural / metadata
    importance_score: float = 0.0
    time_score: float = 0.0
    entity_boost: float = 0.0
    penalty_mult: float = 1.0  # 0.0-1.0, applied after entity_boost (hard_filter demotions)

    # Raw row/ES source for enrichment
    raw: Any = None
    es_source: dict = field(default_factory=dict)

    # Ranks per channel (for score breakdown)
    bm25_rank: int | None = None
    vector_rank: int | None = None
    name_match_rank: int | None = None

    # PG ID for dedup (some tables use string, others use int)
    pg_id: str = ""

    # Per-stage score traceability
    score_breakdown: dict = field(default_factory=dict)

    def __hash__(self):
        return hash((self.table_name, self.id))

    def __eq__(self, other):
        if not isinstance(other, Candidate):
            return False
        return self.table_name == other.table_name and self.id == other.id


@dataclass
class RankedItem:
    """A fully-ranked result item ready for API response."""
    result_type: str
    id: str
    title: str
    snippet: str
    time: datetime | None = None
    score: dict = field(default_factory=dict)


@dataclass
class SearchContext:
    """Mutable context bag passed through all pipeline stages.

    Each stage reads/writes fields on this object. This avoids threading
    a dozen arguments through every function call.
    """
    search_id: str = ""
    query_text: str = ""
    query_norm: str = ""
    mode: str = "hybrid"
    limit: int = 20
    date_range: dict | None = None
    filters: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    target_tables: list[str] = field(default_factory=list)

    # Long query: flag for relaxed BM25 query construction (true when >= 100 chars)
    bm25_is_long: bool = False

    # Stage outputs
    entities: list[EntityResult] = field(default_factory=list)
    main_entity: EntityResult | None = None
    expanded_keywords: set[str] = field(default_factory=set)
    entity_context: dict[str, Any] = field(default_factory=dict)
    time_bias_days: int | None = None  # inferred from query (e.g. "今天" → 3)
    # Keywords that triggered time_bias — stripped from ES query in recall stage
    temporal_keywords: set[str] = field(default_factory=set)

    candidates: list[Candidate] = field(default_factory=list)
    filtered_count: dict = field(default_factory=dict)  # {reason: count}

    final_items: list[RankedItem] = field(default_factory=list)
    total_hits: int = 0

    # Timing / metadata per stage (values may be float, str, or dict)
    timings: dict[str, Any] = field(default_factory=dict)

    # Structured search quality metrics for offline analysis / A/B testing
    search_quality_metrics: dict = field(default_factory=dict)
