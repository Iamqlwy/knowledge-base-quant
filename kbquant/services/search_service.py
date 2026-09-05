"""搜索服务 — 7 阶段流水线编排层。

阶段：
1. entity_resolver  - 实体识别
2. query_rewriter   - 查询改写
3. recall_service   - 多路召回
4. hard_filter      - 硬过滤
5. fusion_service   - RRF 融合
6. rerank_service   - 重排序
7. final_ranking    - 最终排序

保留原有 public API（search / fetch_by_ids）兼容 API 路由，
内部委托给搜索子模块。
"""
import asyncio
import json
import logging
import time
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from kbquant.config import settings
from kbquant.database import LazyDB
from kbquant.integrations.elasticsearch.client import get_es
from kbquant.models.raw_information import RawInformation
from kbquant.models.analysis import Analysis
from kbquant.models.feedback import Feedback
from kbquant.models.trading_operation import TradingOperation
from kbquant.models.world_node import WorldNode
from kbquant.models.node_state import NodeState
from kbquant.models.search_candidate import SearchContext
from kbquant.schemas import _serialize_value
from dateutil.parser import parse as date_parse

logger = logging.getLogger(__name__)
PREFIX = settings.elasticsearch_index_prefix

# ---------------------------------------------------------------------------
# 分片搜索缓存 — 降低锁竞争
# ---------------------------------------------------------------------------
_SEARCH_CACHE_TTL = 30.0
_SEARCH_CACHE_MAXSIZE = 512
_SEARCH_NUM_SHARDS = 8


class _SearchShard:
    __slots__ = ("lock", "cache")

    def __init__(self):
        self.lock = asyncio.Lock()
        self.cache: dict[tuple, tuple[float, dict]] = {}

    def clear(self):
        self.cache.clear()


_search_shards = [_SearchShard() for _ in range(_SEARCH_NUM_SHARDS)]
_SEARCH_PER_SHARD_MAX = max(32, _SEARCH_CACHE_MAXSIZE // _SEARCH_NUM_SHARDS)


def _get_search_shard(key: tuple) -> _SearchShard:
    return _search_shards[hash(key) % _SEARCH_NUM_SHARDS]


class SearchService:

    def __init__(self, db: LazyDB):
        self.db = db

    # ------------------------------------------------------------------
    # Static helpers (kept here for backward compatibility with recall_service)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_bm25_bool_query(
        query_text: str,
        search_fields: list[str],
        should_clauses: list | None = None,
        is_long_query: bool = False,
    ) -> dict:
        """Construct a bool-should query that prioritises exact & phrase matches.

        For long queries (document-as-query use case), uses a single lenient
        should clause instead of phrase/AND/70% tiers that can never match.
        """
        if is_long_query:
            should: list[dict] = [
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": search_fields,
                        "minimum_should_match": "30%",
                        "boost": 1.0,
                    }
                },
            ]
        else:
            should: list[dict] = [
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": search_fields,
                        "type": "phrase",
                        "boost": 4.0,
                    }
                },
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": search_fields,
                        "operator": "and",
                        "boost": 2.0,
                    }
                },
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": search_fields,
                        "minimum_should_match": "70%",
                        "boost": 1.0,
                    }
                },
            ]
        if should_clauses:
            should.extend(should_clauses)

        if not is_long_query and len(query_text) >= 5 and query_text.isascii():
            should.append(
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": search_fields,
                        "fuzziness": "AUTO",
                        "boost": 0.3,
                    }
                }
            )

        return {
            "bool": {
                "should": should,
                "minimum_should_match": 1,
                "filter": [],
            }
        }

    @staticmethod
    async def _es_bm25_search(query_text: str, index: str,
                              search_fields: list[str], filters: dict | None = None,
                              limit: int = 20,
                              should_clauses: list | None = None,
                              date_range: dict | None = None,
                              date_field: str = "published_at",
                              is_long_query: bool = False) -> dict[str, dict]:
        es = get_es()
        query_part = SearchService._build_bm25_bool_query(
            query_text, search_fields, should_clauses, is_long_query=is_long_query
        )
        body = {
            "query": query_part,
            "size": limit,
        }

        if filters:
            for field, value in filters.items():
                if value is not None:
                    body["query"]["bool"]["filter"].append({"term": {field: value}})

        if date_range:
            range_clause: dict[str, str] = {}
            if date_range.get("start"):
                range_clause["gte"] = str(date_range["start"])
            if date_range.get("end"):
                range_clause["lte"] = str(date_range["end"])
            if range_clause:
                body["query"]["bool"]["filter"].append({"range": {date_field: range_clause}})

        try:
            async with asyncio.timeout(settings.es_request_timeout + 5):
                resp = await es.options(request_timeout=settings.es_request_timeout).search(
                    index=index,
                    body=body,
                )
        except Exception as exc:
            logger.debug("ES 搜索失败 index=%s: %s", index, exc)
            return {}

        results = {}
        for hit in resp["hits"]["hits"]:
            pg_id = hit["_source"].get("pg_id")
            if pg_id:
                results[pg_id] = {"score": hit["_score"], "source": hit["_source"]}
        return results

    _VECTOR_FIELDS = (
        "id", "title", "body", "content", "lessons_learned", "description",
        "published_at", "created_at", "updated_at", "effective_from",
        "importance_score", "name", "node_type", "state_summary",
        "core_logic", "recent_changes", "primary_drivers", "risks",
        "focus_points", "uncertainty_flags", "node_id",
    )

    @staticmethod
    async def _pg_vector_search(query_embedding: list[float], table_class,
                                limit: int = 20, session: AsyncSession | None = None,
                                date_range: dict | None = None) -> dict[str, dict]:
        col = getattr(table_class, "embedding", None)
        if col is None:
            return {}
        distance_col = col.cosine_distance(query_embedding).label("_distance")
        stmt = select(table_class, distance_col).where(col.is_not(None))

        pub_col = (
            getattr(table_class, "published_at", None)
            or getattr(table_class, "created_at", None)
            or getattr(table_class, "updated_at", None)
            or getattr(table_class, "effective_from", None)
        )

        if pub_col is not None and date_range:
            if date_range.get("start"):
                parsed_start = date_parse(str(date_range["start"]))
                if parsed_start.tzinfo is None:
                    parsed_start = parsed_start.replace(tzinfo=timezone.utc)
                stmt = stmt.where(pub_col >= parsed_start)
            if date_range.get("end"):
                parsed_end = date_parse(str(date_range["end"]))
                if parsed_end.tzinfo is None:
                    parsed_end = parsed_end.replace(tzinfo=timezone.utc)
                stmt = stmt.where(pub_col <= parsed_end)

        stmt = stmt.order_by(distance_col).limit(limit)
        try:
            results = await session.execute(stmt)
        except Exception as exc:
            logger.debug("向量搜索失败 table=%s: %s", table_class.__name__, exc)
            return {}
        out = {}
        fields = SearchService._VECTOR_FIELDS
        for row, distance in results.all():
            score = round(1.0 / (1.0 + float(distance)), 4)
            row_dict: dict = {}
            for f in fields:
                val = getattr(row, f, None)
                if f == "id":
                    row_dict[f] = str(val)
                elif f == "node_id" and val:
                    row_dict[f] = str(val)
                else:
                    row_dict[f] = val
            row_dict["__class_name__"] = type(row).__name__
            out[str(row.id)] = {"score": score, "row": row_dict}
        return out

    @staticmethod
    async def _entity_match_search(query_text: str, session: AsyncSession, limit: int = 20) -> dict[str, dict]:
        """Match WorldNode by exact name, ticker, or alias.

        Scoring priority:
          ticker exact      = 1.2
          name exact        = 1.0
          alias exact       = 0.95
          name startswith   = 0.8
          alias contains    = 0.7
          name contains     = 0.5
        """
        stripped = query_text.strip()
        if not stripped:
            return {}
        escaped = stripped.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query_lower = stripped.lower()

        results = await session.execute(
            select(WorldNode)
            .where(
                WorldNode.is_active == True,  # noqa: E712
                or_(
                    WorldNode.name.ilike(f"{escaped}%"),
                    WorldNode.ticker.ilike(f"{escaped}%"),
                    WorldNode.name.ilike(f"%{escaped}%"),
                    WorldNode.ticker.ilike(f"%{escaped}%"),
                    WorldNode.aliases.any(stripped),
                    WorldNode.aliases.any(f"%{escaped}%"),
                )
            )
            .limit(limit)
        )
        out: dict[str, dict] = {}
        for row in results.scalars().all():
            name_lower = (row.name or "").lower().strip()
            ticker = (row.ticker or "").lower().strip()
            aliases_lower = [a.lower().strip() for a in (row.aliases or [])]

            if ticker and ticker == query_lower:
                score = 1.2   # exact ticker
            elif name_lower == query_lower:
                score = 1.0   # exact name
            elif query_lower in aliases_lower:
                score = 0.95  # exact alias
            elif name_lower.startswith(query_lower) and len(query_lower) >= 3:
                score = 0.8   # name prefix (min 3 chars to avoid spurious matches)
            elif any(query_lower in a for a in aliases_lower):
                score = 0.7   # alias contains
            elif query_lower in name_lower:
                score = 0.5   # name contains
            else:
                continue

            out[str(row.id)] = {"score": score, "row": row}
        return out

    _vector_tables = {"raw_information", "analyses", "nodes", "feedbacks"}

    # ------------------------------------------------------------------
    # 流水线搜索入口
    # ------------------------------------------------------------------

    async def search(self, query_text: str, mode: str = "hybrid",
                     filters: dict | None = None, weights: dict | None = None,
                     limit: int = 20, date_range: dict | None = None,
                     only_tables: list[str] | None = None) -> dict:
        search_id = _uuid.uuid4().hex[:12]

        # Normalize query
        query_norm = " ".join(query_text.strip().lower().split())
        _dr_key = json.dumps(date_range, sort_keys=True, default=str) if date_range else ""
        _filters_key = json.dumps(filters or {}, sort_keys=True, default=str)
        _weights_key = json.dumps(weights or {}, sort_keys=True, default=str)

        # Feature flags
        ff = settings.feature_flags
        ff_entity = ff.get("entity_resolver", True)
        ff_query = ff.get("query_rewriter", True)
        ff_hard = ff.get("hard_filter", True)
        ff_rerank = ff.get("rerank", True)
        ff_rerank_fallback = ff.get("rerank_fallback_on_error", True)
        ff_dynamic_weights = ff.get("dynamic_weights", True)
        ff_empty_content = ff.get("empty_content_filter", True)  # used by _get_default_rules
        ff_reranker_threshold = ff.get("reranker_threshold_filter", True)
        ff_analysis_type_boost = ff.get("analysis_query_type_boost", True)
        ff_market_region = ff.get("market_region_boost", True)  # used by _get_default_rules
        _use_dynamic = ff_dynamic_weights and weights is None

        # Cache: when using dynamic weights, entity resolution and weight
        # computation affect results — include those outputs in the key.
        # We use a two-phase approach: try static-key cache first when
        # weights are explicit; when dynamic, only write cache at end
        # with a key that includes computed entity/weight state.
        cache_key = None
        shard = None

        async def _try_cache(key_tuple):
            """Check sharded cache; return (hit: bool, value | None)."""
            nonlocal shard
            if shard is None:
                shard = _get_search_shard(key_tuple)
            async with shard.lock:
                cached = shard.cache.get(key_tuple)
                if cached is not None:
                    ts, val = cached
                    if time.monotonic() - ts < _SEARCH_CACHE_TTL:
                        shard.cache[key_tuple] = shard.cache.pop(key_tuple)
                        return True, val
                    else:
                        del shard.cache[key_tuple]
            return False, None

        if not _use_dynamic:
            cache_key = (
                query_norm, mode, _filters_key, _weights_key, limit, _dr_key,
            )
            hit, val = await _try_cache(cache_key)
            if hit and val is not None:
                return val

        started_at = time.perf_counter()

        # Build search context
        ctx = SearchContext(
            search_id=search_id,
            query_text=query_text,
            query_norm=query_norm,
            mode=mode,
            limit=limit,
            date_range=date_range,
            filters=filters or {},
            weights=weights or {"bm25": 1.2, "vector": 1.0, "name_match": 2.5, "structural": 0.2, "time_decay": 0.25},
        )

        # ---- Stage 2.5a: BM25 long query detection ----
        # Long queries (>= 100 chars) trigger relaxed ES query construction
        # in _build_bm25_bool_query via the bm25_is_long flag.
        ctx.bm25_is_long = len(query_norm) >= 100
        if ctx.bm25_is_long:
            logger.info(
                "[%s] stage=bm25_long_query_detect orig_len=%d",
                search_id,
                len(query_norm),
            )

        # ── Single DB session for stages 1–3 ──────────────────────────
        # Entity resolution, vector recall, and name-match recall all need
        # the database.  We open one session and reuse it across stages
        # instead of three independent sessions (reducing pool churn 3×).
        async with self.db.session() as db_session:
            # ---- Stage 1: Entity Resolver ----
            t1 = time.perf_counter()
            if ff_entity:
                from kbquant.services.search.entity_resolver import EntityResolver
                entity_resolver = EntityResolver()
                _ = await entity_resolver.resolve(query_text, session=db_session, ctx=ctx)
            ctx.timings["entity_resolver"] = round(time.perf_counter() - t1, 4)

            # ---- Stage 2: Query Rewriter ----
            t2 = time.perf_counter()
            if ff_query:
                from kbquant.services.search.query_rewriter import QueryRewriter
                query_rewriter = QueryRewriter()
                _ = await query_rewriter.rewrite(query_text, ctx=ctx)
            ctx.timings["query_rewriter"] = round(time.perf_counter() - t2, 4)

            # ---- Stage 2.5: Dynamic Weights (after entity + query rewrite) ----
            if ff_dynamic_weights and weights is None:
                from kbquant.services.search.dynamic_weights import compute_dynamic_weights
                dynamic_weights, query_features = compute_dynamic_weights(query_text, ctx)
                ctx.weights = dynamic_weights
                ctx.timings["dynamic_weights_intent"] = query_features.intent
                ctx.timings["dynamic_weights"] = {
                    "intent": query_features.intent,
                    "intent_confidence": round(query_features.intent_confidence, 3),
                    "time_sensitivity": round(query_features.time_sensitivity, 3),
                    "entity_specificity": round(query_features.entity_specificity, 3),
                    "domains": query_features.domains[:2],
                }
                # Pass region detection through to special rules
                ctx.timings["target_region"] = query_features.target_region
                ctx.timings["target_region_confidence"] = query_features.target_region_confidence
                logger.info(
                    "[%s] stage=dynamic_weights intent=%s(%.2f) time=%.2f entity=%.2f domains=%s weights=%s",
                    search_id,
                    query_features.intent,
                    query_features.intent_confidence,
                    query_features.time_sensitivity,
                    query_features.entity_specificity,
                    query_features.domains[:2],
                    dynamic_weights,
                )

                # Dynamic weights cache: when intent confidence is high,
                # the output is deterministic enough to cache.
                if query_features.intent_confidence > 0.7:
                    cache_key = (
                        query_norm, mode, _filters_key, limit, _dr_key,
                        "dynamic", query_features.cache_fingerprint(),
                    )
                    hit, val = await _try_cache(cache_key)
                    if hit and val is not None:
                        return val

            # ---- Stage 3: Multi-Recall (uses the same db_session) ----
            t3 = time.perf_counter()
            from kbquant.services.search.recall_service import RecallService
            recall_service = RecallService()

            if only_tables:
                _valid = {
                    "raw_information", "analyses", "nodes", "feedbacks",
                    # aliases
                    "raw", "raw_info", "info", "information", "news",
                    "analysis", "report", "reports", "研报",
                    "node", "worldnode", "world_nodes", "graph", "knowledge",
                    "feedback", "lesson", "lessons", "复盘",
                }
                _alias_to_table = {
                    "raw": "raw_information", "raw_info": "raw_information",
                    "info": "raw_information", "information": "raw_information",
                    "news": "raw_information",
                    "analysis": "analyses", "report": "analyses",
                    "reports": "analyses", "研报": "analyses",
                    "node": "nodes", "worldnode": "nodes", "world_nodes": "nodes",
                    "graph": "nodes", "knowledge": "nodes",
                    "feedback": "feedbacks", "lesson": "feedbacks",
                    "lessons": "feedbacks", "复盘": "feedbacks",
                }
                resolved: list[str] = []
                seen: set[str] = set()
                for t in only_tables:
                    t = t.strip().lower()
                    if not t:
                        continue
                    if t in _alias_to_table:
                        canonical = _alias_to_table[t]
                    elif t in _valid:
                        canonical = t
                    else:
                        continue
                    if canonical not in seen:
                        seen.add(canonical)
                        resolved.append(canonical)
                target_tables = resolved
                if not target_tables:
                    # All aliases were invalid — fall back to raw_information
                    target_tables = ["raw_information"]
            else:
                target_tables = recall_service.determine_tables(query_text, ctx.entities, ctx)

            _effective_date_range = dict(date_range) if date_range else {}
            if ctx.time_bias_days is not None and "start" not in _effective_date_range:
                # For long queries, skip time_bias date filtering — the query
                # text contains incidental time words that don't reflect the
                # user's actual recency intent.
                if not ctx.bm25_is_long:
                    from datetime import timedelta
                    _effective_date_range["start"] = (datetime.now(timezone.utc) - timedelta(days=ctx.time_bias_days)).isoformat()

            bm25_results: dict[str, dict] = {}
            pg_results: dict[str, dict] = {}
            name_match_results: dict[str, dict] = {}

            _recall_max = getattr(settings, "search_recall_limit_max", 200)
            recall_limit = min(max(limit * 5, 100), _recall_max)

            # Strip temporal qualifier keywords (今日/昨天/本周 etc.)
            # from the ES query — they already informed time_bias, keeping
            # them in the query text only matches irrelevant document titles.
            temporal_keywords = ctx.temporal_keywords if ctx else set()
            recall_query = (
                " ".join(w for w in query_text.split() if w not in temporal_keywords)
                if temporal_keywords else query_text
            )

            bm25_results, pg_results, name_match_results = await recall_service.recall(
                recall_query, target_tables=target_tables,
                date_range=_effective_date_range if _effective_date_range else None, limit=recall_limit,
                session=db_session, ctx=ctx,
            )
            ctx.timings["recall"] = round(time.perf_counter() - t3, 4)

        # ---- Stage 4: Hard Filter (BEFORE fusion, operates on raw results) ----
        t4 = time.perf_counter()
        hard_filter = None
        if ff_hard:
            from kbquant.services.search.hard_filter import HardFilter
            hard_filter = HardFilter()
            bm25_results, pg_results, name_match_results, _ = hard_filter.filter_raw_results(
                bm25_results, pg_results, name_match_results, ctx=ctx,
            )
        ctx.timings["hard_filter"] = round(time.perf_counter() - t4, 4)

        # ---- Stage 5: RRF Fusion ----
        t5 = time.perf_counter()
        from kbquant.services.search.fusion_service import FusionService
        fusion_service = FusionService(weights=ctx.weights)
        candidates = fusion_service.fuse(
            bm25_results, pg_results, name_match_results, ctx=ctx,
        )
        ctx.timings["rrf"] = round(time.perf_counter() - t5, 4)

        # ---- Post-fusion soft boosts (keyword coverage, entity demotion) ----
        # These apply regardless of ff_hard — they're scoring adjustments, not filters.
        if hard_filter is None:
            from kbquant.services.search.hard_filter import HardFilter
            hard_filter = HardFilter()
        candidates = hard_filter.apply_boosts(candidates, ctx=ctx)

        # ---- Snippet extraction (between fusion and rerank, per spec) ----
        try:
            from kbquant.services.search.snippet_service import SnippetService
            snippet_service = SnippetService()
            candidates = snippet_service.extract(query_text, candidates)
        except Exception as exc:
            logger.warning("Snippet extraction failed: %s", exc)

        # ---- Stage 6: Rerank (skip for bm25-only mode) ----
        t6 = time.perf_counter()
        rerank_applied = False
        if ff_rerank and mode != "bm25":
            try:
                from kbquant.services.search.rerank_service import RerankService
                rerank_service = RerankService()
                candidates = await rerank_service.rerank(query_text, candidates, ctx=ctx)
                rerank_applied = True
            except Exception as exc:
                logger.warning("Rerank stage failed, fallback: %s", exc)
                if ff_rerank_fallback:
                    pass
                else:
                    raise
        ctx.timings["rerank"] = round(time.perf_counter() - t6, 4)

        # ---- Stage 7: Final Ranking ----
        t7 = time.perf_counter()
        from kbquant.services.search.final_ranking import FinalRanking
        final_ranking = FinalRanking()
        candidates = final_ranking.rank(
            candidates, ctx=ctx,
            reranker_threshold_filter=ff_reranker_threshold,
            analysis_query_type_boost=ff_analysis_type_boost,
        )
        ctx.timings["final"] = round(time.perf_counter() - t7, 4)

        # ---- Stage 7.5: Special Rules ----
        t7_5 = time.perf_counter()
        from kbquant.services.search.special_rules import SpecialRules, _get_default_rules
        special_rules = SpecialRules(rules=_get_default_rules())
        candidates = await special_rules.apply(candidates, ctx=ctx)
        ctx.timings["special_rules"] = round(time.perf_counter() - t7_5, 4)

        # ---- Truncate + convert format ----
        top = candidates[:limit]
        ctx.total_hits = len(candidates)

        node_ids = [c.id for c in top if c.result_type == "node"]
        node_data: dict[str, dict] = {}
        if node_ids and "nodes" in target_tables:
            async with self.db.session() as db_session:
                node_uuids = [_uuid.UUID(pid) for pid in node_ids]
                node_result = await db_session.execute(
                    select(WorldNode).where(WorldNode.id.in_(node_uuids))
                )
                state_result = await db_session.execute(
                    select(NodeState).where(
                        NodeState.node_id.in_(node_uuids),
                        NodeState.effective_to == None,
                    ).options(defer(NodeState.embedding))
                )
                node_map = {str(r.id): r for r in node_result.scalars().all() if r.is_active}
                state_map: dict[str, NodeState] = {}
                for sr in state_result.scalars().all():
                    state_map[str(sr.node_id)] = sr
                for pid in node_ids:
                    nd = node_map.get(pid)
                    if nd:
                        st = state_map.get(pid)
                        node_data[pid] = {
                            "name": nd.name,
                            "node_type": nd.node_type,
                            "core_logic": st.core_logic if st else None,
                            "state_summary": st.state_summary if st else None,
                            "updated_at": nd.updated_at,
                        }

        # Build final items
        items = []
        for c in top:
            result_type = c.result_type
            title = c.title
            snippet = c.snippet
            time_value: datetime | None = c.time

            if result_type == "node" and c.id in node_data:
                nd = node_data[c.id]
                title = nd["name"]
                snippet = nd["state_summary"] or nd["core_logic"] or ""
                if not time_value:
                    time_value = nd.get("updated_at")

            if not time_value:
                pg_hit = pg_results.get(c.id)
                es_hit = bm25_results.get(c.id)
                if es_hit:
                    src = es_hit.get("source", {})
                    pub_str = src.get("published_at") or src.get("created_at")
                    if pub_str:
                        try:
                            time_value = date_parse(pub_str)
                        except Exception:
                            pass
                if not time_value and pg_hit and pg_hit.get("row"):
                    row = pg_hit["row"]
                    time_value = (
                        row.get("published_at")
                        or row.get("created_at")
                        or row.get("updated_at")
                        or row.get("effective_from")
                    )

            items.append({
                "result_type": result_type,
                "id": c.id,
                "title": title,
                "snippet": snippet,
                "time": time_value,
                "score": {
                    "total": round(c.final_score, 4),
                    "bm25_rank": c.bm25_rank,
                    "vector_rank": c.vector_rank,
                    "structural": round(c.importance_score, 4),
                    "time_score": round(c.time_score, 4),
                    "reranker": round(c.reranker_score, 4) if c.reranker_score > 0 else None,
                    "breakdown": c.score_breakdown,
                },
            })

        result = {"items": items, "total": ctx.total_hits}

        # Cache write (both static and dynamic-weight caches)
        if cache_key is not None and shard is not None:
            async with shard.lock:
                if len(shard.cache) >= _SEARCH_PER_SHARD_MAX:
                    shard.cache.pop(next(iter(shard.cache)))
                shard.cache[cache_key] = (time.monotonic(), result)

        # Structured log (per spec)
        _t_total = time.perf_counter() - started_at
        logger.info(
            "[%s] stage=entity_resolver entities=%d main=%s(%s)",
            search_id,
            len(ctx.entities),
            ctx.main_entity.name if ctx.main_entity else "none",
            ctx.main_entity.entity_type if ctx.main_entity else "",
        )
        logger.info(
            "[%s] stage=query_rewriter keywords=%d entity_context=%d",
            search_id,
            len(ctx.expanded_keywords),
            len(ctx.entity_context),
        )
        logger.info(
            "[%s] stage=recall tables=%d bm25=%d vector=%d name_match=%d merged=%d",
            search_id,
            len(target_tables),
            len(bm25_results),
            len(pg_results),
            len(name_match_results),
            len(bm25_results) + len(pg_results) + len(name_match_results),
        )
        logger.info(
            "[%s] stage=hard_filter dropped=%d reasons=%s",
            search_id,
            sum(ctx.filtered_count.values()),
            ctx.filtered_count,
        )
        logger.info(
            "[%s] stage=rrf top_%d=[%s]",
            search_id,
            min(50, len(candidates)),
            ",".join(c.id[:12] for c in candidates[:5] if c.id),
        )
        logger.info(
            "[%s] stage=rerank applied=%s latency=%sms",
            search_id,
            rerank_applied,
            int(ctx.timings.get("rerank", 0) * 1000),
        )
        top_titles = [c.title[:30] if c.title else "?" for c in candidates[:3]]
        logger.info(
            "[%s] stage=final top_3=%s",
            search_id,
            top_titles,
        )
        logger.debug(
            "search total=%.4fs mode=%s tables=%s recall=%d return=%d "
            "bm25=%d vec=%d name=%d candidates=%d timings=%s",
            _t_total, mode, target_tables, recall_limit, limit,
            len(bm25_results), len(pg_results), len(name_match_results),
            ctx.total_hits, ctx.timings,
        )

        # Collect structured search quality metrics
        ctx.search_quality_metrics = {
            "search_id": search_id,
            "query_length": len(query_norm),
            "intent": ctx.timings.get("dynamic_weights_intent", "general"),
            "total_candidates": ctx.total_hits,
            "returned": len(top),
            "score_distribution": {
                "min": round(min(c.final_score for c in top), 6) if top else 0,
                "max": round(max(c.final_score for c in top), 6) if top else 0,
                "mean": round(sum(c.final_score for c in top) / len(top), 6) if top else 0,
            },
            "result_types": {
                t: len([c for c in top if c.result_type == t])
                for t in sorted(set(c.result_type for c in top))
            },
            "rerank_applied": rerank_applied,
        }
        logger.info(
            "[%s] search_quality_metrics=%s",
            search_id,
            json.dumps(ctx.search_quality_metrics, default=str, ensure_ascii=False),
        )

        return result

    # ------------------------------------------------------------------
    # fetch_by_ids — kept unchanged
    # ------------------------------------------------------------------

    _table_registry = {
        "raw_information": RawInformation,
        "analyses": Analysis,
        "feedbacks": Feedback,
        "trading_operations": TradingOperation,
        "world_nodes": WorldNode,
        "node_states": NodeState,
    }

    @staticmethod
    def _model_to_dict(row) -> dict:
        return {
            c.name: _serialize_value(getattr(row, c.name))
            for c in row.__table__.columns
            if c.name not in ("embedding",)
        }

    async def fetch_by_ids(self, table_ids: dict[str, list]) -> dict[str, list[dict]]:
        async def _fetch_table(table_name: str, ids: list) -> tuple[str, list[dict]]:
            async with self.db.session() as session:
                if table_name == "nodes":
                    return "nodes", await self._fetch_nodes_by_ids(ids, session=session)
                model = self._table_registry.get(table_name)
                if model is None or not ids:
                    return table_name, []
                rows = await session.execute(
                    select(model).where(model.id.in_(ids))
                )
                return table_name, [self._model_to_dict(r) for r in rows.scalars().all()]

        results = await asyncio.gather(*[
            _fetch_table(table_name, ids)
            for table_name, ids in table_ids.items()
        ])
        return {table_name: rows for table_name, rows in results}

    @staticmethod
    async def _fetch_nodes_by_ids(ids: list, session: AsyncSession) -> list[dict]:
        if not ids:
            return []
        uuids = [_uuid.UUID(str(x)) for x in ids]
        node_result = await session.execute(
            select(WorldNode).where(WorldNode.id.in_(uuids))
        )
        state_result = await session.execute(
            select(NodeState).where(
                NodeState.node_id.in_(uuids),
                NodeState.effective_to == None,
            ).options(defer(NodeState.embedding))
        )
        nodes = {str(r.id): r for r in node_result.scalars().all() if r.is_active}
        states = {}
        for sr in state_result.scalars().all():
            states[str(sr.node_id)] = sr
        result = []
        for uid in ids:
            node = nodes.get(str(uid))
            if not node:
                continue
            st = states.get(str(uid))
            result.append({
                "id": str(node.id),
                "name": node.name,
                "node_type": node.node_type,
                "description": node.description,
                "ticker": node.ticker,
                "aliases": node.aliases,
                "metadata_": node.metadata_,
                "is_active": node.is_active,
                "created_at": node.created_at.isoformat() if node.created_at else None,
                "current_state": {
                    "core_logic": st.core_logic,
                    "primary_drivers": st.primary_drivers,
                    "risks": st.risks,
                    "focus_points": st.focus_points,
                    "recent_changes": st.recent_changes,
                    "uncertainty_flags": st.uncertainty_flags,
                    "key_evidence_ids": [str(x) for x in st.key_evidence_ids] if st.key_evidence_ids else None,
                    "state_summary": st.state_summary,
                } if st else None,
            })
        return result
