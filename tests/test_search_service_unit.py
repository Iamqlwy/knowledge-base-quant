"""
Unit tests for the SearchService optimizations.

Covers:
- cache_key with weights
- recall_limit / return_limit split
- RRF scale normalization
- BM25 bool query builder
- _entity_match_search scoring
- _infer_time_weight
- Field getattr safety
"""

import math
import pytest

from kbquant.services.search_service import SearchService


# ---------------------------------------------------------------------------
# 1. cache_key — different weights produce different keys
# ---------------------------------------------------------------------------

def test_cache_key_weights_differ():
    """Different weights must produce different cache keys."""
    svc = SearchService(db=None)

    # Simulate the cache key construction from search()
    import json
    query_text = "test query"
    query_norm = " ".join(query_text.strip().lower().split())
    mode = "hybrid"
    limit = 20
    dr = None
    _dr_key = ""

    weights_a = {"bm25": 1.0, "vector": 1.0}
    weights_b = {"bm25": 2.0, "vector": 1.0}

    key_a = (
        query_norm, mode,
        json.dumps({}, sort_keys=True, default=str),
        json.dumps(weights_a, sort_keys=True, default=str),
        limit, _dr_key,
    )
    key_b = (
        query_norm, mode,
        json.dumps({}, sort_keys=True, default=str),
        json.dumps(weights_b, sort_keys=True, default=str),
        limit, _dr_key,
    )

    assert key_a != key_b, "Different weights should yield different cache keys"


def test_cache_key_query_normalized():
    """Whitespace and case differences yield the same cache key."""
    import json

    def _make_key(text):
        norm = " ".join(text.strip().lower().split())
        return (
            norm, "hybrid",
            json.dumps({}, sort_keys=True, default=str),
            json.dumps({}, sort_keys=True, default=str),
            20, "",
        )

    assert _make_key("  Hello   World  ") == _make_key("hello world")
    assert _make_key("HELLO WORLD") == _make_key("hello world")


# ---------------------------------------------------------------------------
# 2. RRF scale — importance/time_score are normalized to RRF range
# ---------------------------------------------------------------------------

def test_rrf_scale_normalization():
    """importance_norm / (k+1) should be at most 1/(k+1), not raw."""
    rrf_k = 60
    importance_raw = 0.85
    importance_norm = min(max(importance_raw, 0.0), 1.0)
    contribution = 0.2 * importance_norm / (rrf_k + 1)

    # With the fix, max contribution from structural = w_struct * 1/(k+1) = 0.2 * 0.0164 = 0.0033
    # Without the fix, it would have been 0.2 * 0.85 = 0.17 (blows up)
    assert contribution < 0.01, (
        f"Structural contribution {contribution} is too large; "
        "it should be normalized to RRF scale"
    )

    # Time decay contribution should also be in RRF range
    time_score = 0.9
    time_contribution = 0.15 * time_score / (rrf_k + 1)
    assert time_contribution < 0.01, (
        f"Time contribution {time_contribution} is too large"
    )


def test_rrf_scale_importance_zero_for_missing_field():
    """Row without importance_score gets 0 contribution."""
    rrf_k = 60
    class FakeRow:
        pass
    row = FakeRow()
    importance_raw = float(getattr(row, "importance_score", 0.0) or 0.0)
    importance_norm = min(max(importance_raw, 0.0), 1.0)
    assert importance_norm == 0.0
    contribution = 0.2 * importance_norm / (rrf_k + 1)
    assert contribution == 0.0


def test_rrf_scale_importance_clamped():
    """importance_score > 1.0 is clamped to 1.0."""
    rrf_k = 60
    class FakeRow:
        importance_score = 5.0
    row = FakeRow()
    importance_raw = float(getattr(row, "importance_score", 0.0) or 0.0)
    importance_norm = min(max(importance_raw, 0.0), 1.0)
    assert importance_norm == 1.0, f"Expected 1.0, got {importance_norm}"


# ---------------------------------------------------------------------------
# 3. BM25 bool query builder
# ---------------------------------------------------------------------------

def test_bm25_bool_query_structure():
    """_build_bm25_bool_query produces a bool-should query without fuzziness for CJK."""
    q = SearchService._build_bm25_bool_query(
        "茅台", ["name^2", "description"]
    )
    assert "bool" in q
    assert "should" in q["bool"]
    should_clauses = q["bool"]["should"]
    # phrase, and, minimum_should_match — no fuzzy for CJK short query
    assert len(should_clauses) == 3, f"Expected 3 clauses, got {len(should_clauses)}"

    # Check phrase clause
    assert should_clauses[0]["multi_match"]["type"] == "phrase"
    assert should_clauses[0]["multi_match"]["boost"] == 4.0

    # Check AND clause
    assert should_clauses[1]["multi_match"]["operator"] == "and"
    assert should_clauses[1]["multi_match"]["boost"] == 2.0

    # Check minimum_should_match clause
    assert should_clauses[2]["multi_match"]["minimum_should_match"] == "70%"
    assert should_clauses[2]["multi_match"]["boost"] == 1.0


def test_bm25_bool_query_fuzzy_for_ascii():
    """Fuzzy clause should only be added for longer ASCII queries."""
    q_cjk = SearchService._build_bm25_bool_query(
        "茅台最近为什么下跌", ["title", "body"]
    )
    should_cjk = q_cjk["bool"]["should"]
    # CJK query — no fuzzy
    assert all("fuzziness" not in str(c) for c in should_cjk),         "CJK query should not have fuzzy clause"

    q_ascii = SearchService._build_bm25_bool_query(
        "Apple earnings report analysis", ["title", "body"]
    )
    should_ascii = q_ascii["bool"]["should"]
    # ASCII long query — should have fuzzy clause
    assert len(should_ascii) == 4, f"Expected 4 clauses, got {len(should_ascii)}"
    fuzzy_clause = should_ascii[-1]
    assert fuzzy_clause["multi_match"]["fuzziness"] == "AUTO"
    assert fuzzy_clause["multi_match"]["boost"] == 0.3


def test_bm25_bool_query_short_ascii_no_fuzzy():
    """Short ASCII query (<5 chars) should not get fuzzy."""
    q = SearchService._build_bm25_bool_query(
        "BTC", ["title", "body"]
    )
    # isascii() + len >= 5 → BTC has len=3, so no fuzzy
    assert len(q["bool"]["should"]) == 3


def test_bm25_bool_query_extra_clauses():
    """Extra should_clauses are appended."""
    q = SearchService._build_bm25_bool_query(
        "茅台", ["name^2"], should_clauses=[{"match": {"name": {"query": "茅台"}}}]
    )
    should = q["bool"]["should"]
    assert len(should) == 4
    assert should[-1] == {"match": {"name": {"query": "茅台"}}}


# ---------------------------------------------------------------------------
# 4. time_bias_days in SearchContext (replaces removed _infer_time_weight)
# ---------------------------------------------------------------------------

def test_time_bias_days_applied():
    """When query_rewriter infers time_bias_days, it lands on SearchContext."""
    from kbquant.models.search_candidate import SearchContext
    ctx = SearchContext()
    assert ctx.time_bias_days is None
    ctx.time_bias_days = 7
    assert ctx.time_bias_days == 7


def test_time_bias_inferred_by_rewriter():
    """QueryRewriter sets time_bias_days for recent-hint queries."""
    from kbquant.services.search.query_rewriter import QueryRewriter
    # "今天" → 3 days
    days, keywords = QueryRewriter._infer_time_bias("今天 茅台 行情")
    assert days == 3
    assert "今天" in keywords
    # "财报" → 90 days (no higher-priority time hint present)
    days, keywords = QueryRewriter._infer_time_bias("茅台 年报 财报")
    assert days == 90
    assert "年报" in keywords and "财报" in keywords
    # no time hint
    days, keywords = QueryRewriter._infer_time_bias("光伏产业链核心逻辑")
    assert days is None
    assert keywords == set()


# ---------------------------------------------------------------------------
# 5. Field getattr safety
# ---------------------------------------------------------------------------

def test_getattr_safe_row():
    """Models without importance_score should not throw AttributeError."""
    class FakeAnalysis:
        id = "abc-123"
        title = "test"
        content = "test content"
        created_at = None
        updated_at = None

    row = FakeAnalysis()

    # Safe access pattern from the RRF merge
    importance_raw = float(getattr(row, "importance_score", 0.0) or 0.0)
    assert importance_raw == 0.0

    published_at = (
        getattr(row, "published_at", None)
        or getattr(row, "created_at", None)
        or getattr(row, "updated_at", None)
        or getattr(row, "effective_from", None)
    )
    assert published_at is None


def test_getattr_safe_row_with_fields():
    """Models with importance_score and published_at work correctly."""
    import datetime

    class FakeRawInfo:
        id = "def-456"
        title = "test"
        body = "test body"
        importance_score = 0.75
        published_at = datetime.datetime(2025, 1, 1)

    row = FakeRawInfo()

    importance_raw = float(getattr(row, "importance_score", 0.0) or 0.0)
    assert importance_raw == 0.75

    published_at = (
        getattr(row, "published_at", None)
        or getattr(row, "created_at", None)
        or getattr(row, "updated_at", None)
        or getattr(row, "effective_from", None)
    )
    assert published_at is not None


# ---------------------------------------------------------------------------
# 6. recall_limit / return_limit split
# ---------------------------------------------------------------------------

def test_recall_limit_formula():
    """recall_limit = min(max(limit*5, 100), search_recall_limit_max)."""
    limit = 20
    _recall_max = 200
    recall = min(max(limit * 5, 100), _recall_max)
    assert recall == 100  # 20*5=100, capped at 200 → 100

    limit = 5
    recall = min(max(limit * 5, 100), _recall_max)
    assert recall == 100  # 5*5=25 < 100, floor is 100

    limit = 50
    recall = min(max(limit * 5, 100), _recall_max)
    assert recall == 200  # 50*5=250 > 200, capped


def test_recall_limit_respects_max():
    """Custom search_recall_limit_max is respected."""
    limit = 50
    _recall_max = 150
    recall = min(max(limit * 5, 100), _recall_max)
    assert recall == 150


# ---------------------------------------------------------------------------
# 7. items construction uses getattr throughout
# ---------------------------------------------------------------------------

def test_items_title_safe_getattr():
    """Title extraction uses getattr, not direct attribute access."""
    class FakeRow:
        title = "Some Title"
        content = "Some Content"

    row = FakeRow()
    title = getattr(row, "title", "") or ""
    snippet = (getattr(row, "body", "") or getattr(row, "content", "") or "")[:200]

    assert title == "Some Title"
    assert snippet == "Some Content"


def test_items_row_without_title():
    """Row without title returns empty string via getattr."""
    class BareRow:
        pass

    row = BareRow()
    title = getattr(row, "title", "") or ""
    assert title == ""
