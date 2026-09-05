"""阶段3: 多路召回 - ES BM25 + pgvector + Entity Match 三通道。

BM25 使用 expanded_keywords 构建搜索字符串。
Entity Match 复用阶段1的识别结果。
所有召回并行执行，单个通道/索引失败不影响其他。
"""
import asyncio
import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from kbquant.config import settings
from kbquant.models.raw_information import RawInformation
from kbquant.models.analysis import Analysis
from kbquant.models.feedback import Feedback
from kbquant.models.node_state import NodeState
from kbquant.models.search_candidate import SearchContext
from kbquant.services.embedding_service import embedding_service
from kbquant.services.search_service import SearchService
from kbquant.services.search.table_rules import select_tables

logger = logging.getLogger(__name__)
PREFIX = settings.elasticsearch_index_prefix


class RecallService:
    """阶段3: 多路召回。

    三个通道（始终全部执行）:
    1. ES BM25 - 关键词全文检索 (使用 expanded_keywords)
    2. pgvector - 语义向量相似度搜索 (使用原始 query_text)
    3. Entity Match - 复用阶段1的实体识别结果
    """

    _search_fields: dict[str, list[str]] = {
        "raw_information": ["title^2", "body"],
        "analyses": ["title^2", "content"],
        "feedbacks": ["title^2", "lessons_learned"],
        "nodes": ["name^2", "description", "node_type"],
        "node_states": ["state_summary^2", "core_logic"],
    }

    _table_indices: dict[str, str] = {
        "raw_information": f"{PREFIX}_raw_info",
        "analyses": f"{PREFIX}_analyses",
        "feedbacks": f"{PREFIX}_feedbacks",
        "nodes": f"{PREFIX}_nodes",
        "node_states": f"{PREFIX}_node_states",
    }

    _table_date_fields: dict[str, str] = {
        "raw_information": "published_at",
        "analyses": "created_at",
        "feedbacks": "created_at",
    }

    _vector_table_classes: dict[str, type] = {
        "raw_information": RawInformation,
        "analyses": Analysis,
        "feedbacks": Feedback,
        "nodes": NodeState,
    }

    # Per-table ES multi_match fields for expanded keyword should clauses.
    # Must match the actual ES index mappings for each table.
    _extra_clause_fields: dict[str, list[str]] = {
        "raw_information": ["title^2", "body"],
        "analyses": ["title^2", "content"],
        "feedbacks": ["title^2", "lessons_learned"],
        "nodes": ["name^2", "description", "node_type"],
        "node_states": ["state_summary^2", "core_logic"],
    }

    async def recall(
        self,
        query_text: str,
        target_tables: list[str] | None = None,
        date_range: dict | None = None,
        limit: int = 100,
        session: AsyncSession | None = None,
        ctx: SearchContext | None = None,
    ) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
        """Execute multi-recall (channels per mode).

        Mode-aware: bm25 skips the vector channel; embedding skips BM25.
        Returns: (bm25_results, vector_results, name_match_results)
        """
        if target_tables is None:
            target_tables = ["raw_information"]

        es_filters: dict = {}
        if ctx and ctx.filters:
            es_filters = {
                k: v for k, v in ctx.filters.items()
                if k != "target_tables"
            }

        mode = ctx.mode if ctx else "hybrid"

        es_results: dict[str, dict] = {}
        pg_results: dict[str, dict] = {}
        name_match_results: dict[str, dict] = {}

        # Build coros for enabled channels
        coros = []
        labels = []

        if mode != "embedding":
            coros.append(self._run_bm25_channel(
                query_text, target_tables, es_filters, limit, date_range, ctx,
            ))
            labels.append("bm25")

        if mode != "bm25":
            coros.append(self._run_vector_channel(
                query_text, target_tables, limit, session, date_range,
            ))
            labels.append("vector")

        if ctx:
            coros.append(self._run_name_match_channel(
                target_tables, ctx, session,
            ))
            labels.append("name_match")

        if not coros:
            return es_results, pg_results, name_match_results

        gathered = await asyncio.gather(*coros, return_exceptions=True)
        for label, result in zip(labels, gathered):
            if isinstance(result, Exception):
                logger.warning("%s channel failed: %s", label, result)
            elif label == "bm25":
                es_results = result
            elif label == "vector":
                pg_results = result
            elif label == "name_match":
                name_match_results = result

        return es_results, pg_results, name_match_results

    async def _run_bm25_channel(
        self,
        query_text: str,
        target_tables: list[str],
        es_filters: dict,
        limit: int,
        date_range: dict | None,
        ctx: SearchContext | None,
    ) -> dict[str, dict]:
        """BM25 channel with fallback: if primary query returns 0, retry relaxed."""
        results = await self._do_bm25_search(query_text, target_tables, es_filters, limit, date_range, ctx)

        if not results and target_tables:
            logger.info(
                "recall BM25 fallback triggered: query=%s tables=%s",
                query_text[:120], target_tables,
            )
            relaxed_query = self._relax_query(query_text)
            if relaxed_query != query_text:
                results = await self._do_bm25_search(
                    relaxed_query, target_tables, es_filters, limit, date_range, ctx,
                    relaxed=True,
                )
                if results:
                    logger.info(
                        "recall BM25 fallback: relaxed query=%s returned %d results",
                        relaxed_query[:120], len(results),
                    )

            if not results and "raw_information" not in target_tables:
                if target_tables == ["nodes"]:
                    # Suppress raw_info fallback when caller explicitly
                    # chose nodes-only (e.g. table_rules node_restrict).
                    return results
                fallback_tables = ["raw_information"]
                results = await self._do_bm25_search(
                    relaxed_query, fallback_tables, es_filters, limit, date_range, ctx,
                    relaxed=True,
                )
                if results:
                    logger.info(
                        "recall BM25 fallback: raw_information only returned %d results",
                        len(results),
                    )

            # Last resort: try single-token queries on raw_information
            if not results:
                tokens = [t for t in query_text.strip().split() if len(t) >= 1]
                for token in tokens[:3]:  # try up to 3 single tokens
                    single_results = await self._do_bm25_search(
                        token, ["raw_information"], es_filters, limit, None, ctx,
                        relaxed=True,
                    )
                    if single_results:
                        results = single_results
                        logger.info(
                            "recall BM25 last resort: token=%s returned %d results",
                            token, len(results),
                        )
                        break
        return results

    async def _do_bm25_search(
        self,
        query_text: str,
        target_tables: list[str],
        es_filters: dict,
        limit: int,
        date_range: dict | None,
        ctx: SearchContext | None,
        relaxed: bool = False,
    ) -> dict[str, dict]:
        """Execute BM25 search across target tables with per-table score normalisation.

        relaxed=True: builds a simpler query skipping phrase/AND/70% tiers,
        using only a lenient multi_match. Used as fallback when the primary
        three-tier query returns zero - common for short CJK queries where
        stopword tokenisation breaks restrictive clauses.
        """
        t0 = time.perf_counter()

        bm25_query = query_text
        is_long = bool(ctx and ctx.bm25_is_long)

        coros = []
        table_order: list[str] = []
        for table_name in target_tables:
            extra_clauses = self._build_extra_clauses_for_table(table_name, ctx)
            if table_name == "nodes":
                coros.append(self._search_nodes_es(
                    bm25_query, es_filters, limit, date_range, extra_clauses,
                    is_long_query=is_long or relaxed,
                ))
            elif table_name in self._table_indices:
                coros.append(SearchService._es_bm25_search(
                    bm25_query,
                    self._table_indices[table_name],
                    self._search_fields[table_name],
                    es_filters,
                    limit,
                    date_range=date_range,
                    date_field=self._table_date_fields.get(table_name, ""),
                    should_clauses=extra_clauses,
                    is_long_query=is_long or relaxed,
                ))
            else:
                continue
            table_order.append(table_name)

        if coros:
            per_table_results = await asyncio.gather(*coros, return_exceptions=True)
            # Per-table min-max normalise before merging.
            # Per-index IDF means BM25 scores are not comparable across indexes
            # of different sizes. Normalising each table's scores into [0, 1]
            # before merging makes them comparable.
            normalized_results: dict[str, dict] = {}
            for table_name, r in zip(table_order, per_table_results):
                if isinstance(r, Exception):
                    logger.warning("BM25 search failed for table %s: %s", table_name, r)
                elif isinstance(r, dict) and r:
                    scores = [info["score"] for info in r.values()]
                    score_min = min(scores)
                    score_range = max(scores) - score_min
                    if score_range > 1e-8:
                        for info in r.values():
                            info["score"] = round((info["score"] - score_min) / score_range, 6)
                    else:
                        for info in r.values():
                            info["score"] = 0.5  # all equal
                    normalized_results.update(r)
                elif isinstance(r, dict):
                    normalized_results.update(r)
            results = normalized_results
        else:
            results = {}

        t_es = time.perf_counter() - t0
        if len(results) == 0 and target_tables:
            logger.warning(
                "recall BM25: %.4fs, 0 results across %d tables=%s query=%s",
                t_es, len(target_tables), target_tables, query_text[:120],
            )
        else:
            logger.debug("recall BM25: %.4fs, %d results", t_es, len(results))
        return results

    async def _run_vector_channel(
        self,
        query_text: str,
        target_tables: list[str],
        limit: int,
        session: AsyncSession | None,
        date_range: dict | None,
    ) -> dict[str, dict]:
        """Vector channel with fallback: if primary returns 0, retry relaxed."""
        results = await self._do_vector_search(query_text, target_tables, limit, session, date_range)

        if not results and target_tables:
            relaxed_query = self._relax_query(query_text)
            if relaxed_query != query_text:
                logger.info(
                    "recall Vector fallback: relaxed query=%s",
                    relaxed_query[:120],
                )
                results = await self._do_vector_search(relaxed_query, target_tables, limit, session, date_range)

            if not results and "raw_information" not in target_tables:
                results = await self._do_vector_search(relaxed_query, ["raw_information"], limit, session, date_range)

        return results

    async def _do_vector_search(
        self,
        query_text: str,
        target_tables: list[str],
        limit: int,
        session: AsyncSession | None,
        date_range: dict | None,
    ) -> dict[str, dict]:
        """Vector channel: parallel per-table pgvector search, individual failure isolation."""
        if session is None:
            logger.debug("Vector channel skipped: no DB session")
            return {}

        t0 = time.perf_counter()
        results: dict[str, dict] = {}

        query_embedding = await embedding_service.embed_text(query_text)
        if not query_embedding:
            logger.warning(
                "Vector channel skipped: embedding failed query=%s",
                query_text[:80],
            )
            return {}

        coros = []
        table_order: list[str] = []
        for table_name in target_tables:
            if table_name == "nodes":
                coros.append(self._vector_search_nodes(query_embedding, limit, session))
            elif table_name in self._vector_table_classes:
                model = self._vector_table_classes[table_name]
                coros.append(SearchService._pg_vector_search(
                    query_embedding, model, limit,
                    session=session, date_range=date_range,
                ))
            else:
                continue
            table_order.append(table_name)

        if coros:
            per_table_results = await asyncio.gather(*coros, return_exceptions=True)
            for table_name, r in zip(table_order, per_table_results):
                if isinstance(r, Exception):
                    logger.warning("Vector search failed for table %s: %s", table_name, r)
                elif isinstance(r, dict):
                    if table_name == "nodes":
                        # Nodes results are already deduped by node_id in _vector_search_nodes
                        results.update(r)
                    else:
                        results.update(r)

        t_vec = time.perf_counter() - t0
        logger.debug("recall Vector: %.4fs, %d results", t_vec, len(results))
        return results

    async def _run_name_match_channel(
        self,
        target_tables: list[str],
        ctx: SearchContext | None,
        session: AsyncSession | None,
    ) -> dict[str, dict]:
        """Name-match recall channel: three sources keyed by document UUID.

        1. information_entity → raw_information (high relevance_score rows)
        2. node_state.key_evidence_ids → raw_information / analyses
        3. WorldNode direct (legacy, for nodes table queries)
        """
        results: dict[str, dict] = {}
        if not ctx:
            return results

        want_raw = "raw_information" in target_tables
        want_nodes = "nodes" in target_tables
        if not want_raw and not want_nodes:
            return results

        coros = []

        if want_raw and session is not None:
            coros.append(self._name_match_via_info_entity(ctx, session))
            coros.append(self._name_match_via_key_evidence(ctx, session))

        if want_nodes and session is not None:
            coros.append(self._name_match_via_worldnode(ctx, session))

        if coros:
            gathered = await asyncio.gather(*coros, return_exceptions=True)
            for r in gathered:
                if isinstance(r, Exception):
                    logger.debug("NameMatch sub-channel failed: %s", r)
                elif isinstance(r, dict):
                    for k, v in r.items():
                        if k not in results:
                            results[k] = v

        logger.debug("recall NameMatch: %d results", len(results))
        return results

    @staticmethod
    async def _name_match_via_info_entity(
        ctx: SearchContext,
        session: AsyncSession,
    ) -> dict[str, dict]:
        """Source 1: information_entity → raw_information.

        For each resolved entity with a node_id, find raw_information rows
        that are linked via information_entity with relevance_score >= 0.5.
        These are "anchor" documents that are known to be highly relevant
        to the entity.
        """
        from kbquant.models.information_entity import InformationEntity
        from sqlalchemy import select

        # Collect node_ids from stage-1 entities
        node_ids = [e.node_id for e in ctx.entities if e.node_id]
        if not node_ids:
            return {}

        results: dict[str, dict] = {}
        try:
            rows = await session.execute(
                select(InformationEntity)
                .where(
                    InformationEntity.entity_id.in_(node_ids),
                    InformationEntity.relevance_score >= 0.5,
                )
                .order_by(InformationEntity.relevance_score.desc())
                .limit(50)
            )
            seen: set[str] = set()
            for ie in rows.scalars().all():
                rid = str(ie.raw_info_id)
                if rid in seen:
                    continue
                seen.add(rid)
                nr_score = float(ie.relevance_score or 0.5)
                results[rid] = {
                    "score": 0.8 + nr_score * 0.2,   # range [0.9, 1.0]
                    "row": None,
                    "source": "info_entity",
                }
        except Exception as exc:
            logger.debug("name_match info_entity query failed: %s", exc)

        return results

    @staticmethod
    async def _name_match_via_key_evidence(
        ctx: SearchContext,
        session: AsyncSession,
    ) -> dict[str, dict]:
        """Source 2: node_state.key_evidence_ids → raw_information / analyses.

        For each entity's associated NodeState rows, pull out the
        key_evidence_ids (UUID arrays) and surface those evidence docs.
        """
        from kbquant.models.node_state import NodeState
        from sqlalchemy import select

        node_ids = [e.node_id for e in ctx.entities if e.node_id]
        if not node_ids:
            return {}

        results: dict[str, dict] = {}
        try:
            rows = await session.execute(
                select(NodeState)
                .where(
                    NodeState.node_id.in_(node_ids),
                    NodeState.effective_to.is_(None),   # current version only
                    NodeState.key_evidence_ids.isnot(None),
                )
                .options(defer(NodeState.embedding))
                .limit(50)
            )
            seen: set[str] = set()
            for ns in rows.scalars().all():
                if not ns.key_evidence_ids:
                    continue
                for ev_id in ns.key_evidence_ids:
                    eid = str(ev_id)
                    if eid in seen:
                        continue
                    seen.add(eid)
                    results[eid] = {
                        "score": 0.85,
                        "row": None,
                        "source": "key_evidence",
                    }
        except Exception as exc:
            logger.debug("name_match key_evidence query failed: %s", exc)

        return results

    @staticmethod
    async def _name_match_via_worldnode(
        ctx: SearchContext,
        session: AsyncSession | None = None,
    ) -> dict[str, dict]:
        """Source 3: stage1 entities with node_id → WorldNode key, plus direct query→node name match."""
        results: dict[str, dict] = {}

        for entity in ctx.entities:
            if entity.node_id:
                results[entity.node_id] = {
                    "score": entity.score,
                    "row": None,
                    "entity": entity,
                }

        # Direct query→WorldNode name/ticker/alias matching, independent of entity resolution.
        # This ensures nodes are found even when the entity resolver doesn't
        # identify them (common for sectors, concepts, institutions, etc.).
        if session is not None:
            matched = await SearchService._entity_match_search(
                ctx.query_text, session, limit=10,
            )
            for node_id, info in matched.items():
                new_score = info["score"]
                if node_id in results:
                    results[node_id]["score"] = max(results[node_id]["score"], new_score)
                    if info.get("row") and not results[node_id].get("row"):
                        results[node_id]["row"] = info["row"]
                else:
                    results[node_id] = {
                        "score": new_score,
                        "row": info.get("row"),
                        "source": "direct_match",
                    }

        return results

    @staticmethod
    def _build_extra_clauses_for_table(
        table_name: str,
        ctx: SearchContext | None,
    ) -> list[dict] | None:
        """Build low-boost should clauses for expanded keywords, using the
        correct field list for each table's ES index mapping."""
        if not ctx or not ctx.expanded_keywords:
            return None

        fields = RecallService._extra_clause_fields.get(table_name)
        if not fields:
            return None

        orig_words = set(ctx.query_text.split())
        clauses: list[dict] = []
        for kw in sorted(ctx.expanded_keywords):
            if kw in orig_words:
                continue
            clauses.append({
                "multi_match": {
                    "query": kw,
                    "fields": fields,
                    "boost": 0.3,
                },
            })
        return clauses or None

    async def _search_nodes_es(
        self,
        query_text: str,
        es_filters: dict,
        limit: int,
        date_range: dict | None,
        extra_clauses: list[dict] | None,
        is_long_query: bool = False,
    ) -> dict[str, dict]:
        """Search nodes + node_states indexes in parallel, merge by node_id.

        Nodes take priority over node_states when the same node_id appears in both.
        """
        node_clauses: list[dict] = [
            {"match": {"name": {"query": query_text, "boost": 5.0}}},
            {"match_phrase": {"name": {"query": query_text, "boost": 8.0}}},
        ]
        if extra_clauses:
            node_clauses.extend(extra_clauses)

        node_coro = SearchService._es_bm25_search(
            query_text,
            f"{PREFIX}_nodes",
            ["name^2", "description", "node_type"],
            {**(es_filters or {}), "is_active": True},
            limit,
            should_clauses=node_clauses,
            date_range=date_range,
            date_field="",
            is_long_query=is_long_query,
        )
        state_coro = SearchService._es_bm25_search(
            query_text,
            f"{PREFIX}_node_states",
            ["state_summary^2", "core_logic"],
            es_filters,
            limit,
            date_range=date_range,
            date_field="created_at",
            is_long_query=is_long_query,
        )

        node_es, state_es = await asyncio.gather(
            node_coro, state_coro, return_exceptions=True,
        )

        merged: dict[str, dict] = {}
        if isinstance(node_es, dict):
            merged.update(node_es)
        elif isinstance(node_es, Exception):
            logger.warning("ES nodes search failed: %s", node_es)

        if isinstance(state_es, dict):
            for _, sv in state_es.items():
                nid = sv["source"].get("node_id")
                if nid and nid not in merged:
                    merged[nid] = sv
        elif isinstance(state_es, Exception):
            logger.warning("ES node_states search failed: %s", state_es)

        return merged

    async def _vector_search_nodes(
        self,
        query_embedding: list[float],
        limit: int,
        session: AsyncSession,
    ) -> dict[str, dict]:
        """pgvector search on NodeState, deduped by node_id.

        Enriches NodeState results with WorldNode metadata (name,
        node_type, description) so downstream stages have complete data.
        """
        state_pg = await SearchService._pg_vector_search(
            query_embedding, NodeState, limit, session=session,
        )
        if not state_pg:
            return {}

        # Enrich PG results with WorldNode data (name, node_type, description)
        # so the reranker and other downstream stages have complete metadata.
        from kbquant.models.world_node import WorldNode
        from sqlalchemy import select as sa_select

        node_uuids = []
        for _, sv in state_pg.items():
            row = sv.get("row")
            if row and isinstance(row, dict) and row.get("node_id"):
                try:
                    node_uuids.append(uuid.UUID(row["node_id"]))
                except (ValueError, TypeError, KeyError):
                    pass

        if node_uuids:
            wn_result = await session.execute(
                sa_select(WorldNode).where(
                    WorldNode.id.in_(node_uuids),
                    WorldNode.is_active == True,  # noqa: E712
                )
            )
            wn_map = {str(r.id): r for r in wn_result.scalars().all()}
            for _, sv in state_pg.items():
                row = sv.get("row")
                if row and isinstance(row, dict):
                    nid = row.get("node_id", "")
                    if nid and nid in wn_map:
                        wn = wn_map[nid]
                        row["name"] = wn.name
                        row["node_type"] = wn.node_type
                        row["description"] = wn.description
                        row["updated_at"] = wn.updated_at

        # Dedupe by node_id: keep only one NodeState per WorldNode
        results: dict[str, dict] = {}
        for _, sv in state_pg.items():
            row = sv.get("row")
            if row:
                nid = str(row.get("node_id", "")) if isinstance(row, dict) else str(row.node_id)
                if nid not in results:
                    results[nid] = sv
        return results
    @staticmethod
    def _relax_query(query_text: str) -> str:
        """Relax a CJK query by stripping common stopwords that break AND/70% matching.

        IK analyzer treats single-character particles as stopwords and does not index them.
        When the original query contains these, the AND and 70% clauses fail because
        the required tokens are not in the index. Stripping them produces a query that
        can actually match documents.
        """
        stopwords = {"的", "了", "是", "在", "和", "也", "就", "都", "而", "及", "与",
                     "着", "或", "一个", "没有", "我们", "你们", "他们", "它们",
                     "这", "那", "这个", "那个", "这些", "那些",
                     "有", "不", "被", "把", "让", "从", "对", "以", "到", "所"}
        tokens = [t for t in query_text.strip().split() if t not in stopwords and len(t.strip()) >= 1]
        return " ".join(tokens) if tokens else query_text

    def determine_tables(
        self,
        query_text: str,
        entities: list | None = None,
        ctx: SearchContext | None = None,
    ) -> list[str]:
        """根据实体类型 + 关键词信号 + 语义意图三维度决定搜索哪些表。

        Delegates to table_rules.select_tables for all three dimensions.
        """
        if ctx and ctx.expanded_keywords:
            query_keywords = ctx.expanded_keywords
        else:
            query_keywords = set(query_text.split())

        intent = None
        if ctx and ctx.timings.get("dynamic_weights_intent"):
            intent = ctx.timings["dynamic_weights_intent"]

        sel = select_tables(
            entities=entities,
            query_keywords=query_keywords,
            intent=intent,
            raw_query=query_text,
        )
        result = sel.tables

        if ctx is not None:
            ctx.target_tables = result

        return result
