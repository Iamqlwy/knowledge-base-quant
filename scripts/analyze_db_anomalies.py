#!/usr/bin/env python3
"""
kbquant 数据库非法数据分析脚本
======================
扫描 kbquant 数据库的所有表，检测以下类型的非法/异常数据：
  1. NOT NULL 列包含 NULL 值
  2. 枚举列值超出合法范围
  3. 外键引用完整性（孤儿记录）
  4. 逻辑矛盾（自引用环、时间顺序错误、范围越界等）
  5. 处理管线一致性（1:1 关系断裂、状态不匹配）
  6. UNIQUE 约束违反（理论上不应存在，但防御性检查）
  7. 多态引用完整性（node_attachments / importance_rankings / time_validities）

用法:
  python scripts/analyze_db_anomalies.py
  python scripts/analyze_db_anomalies.py --verbose   # 输出所有检查明细
  python scripts/analyze_db_anomalies.py --db-url "postgresql+asyncpg://user:pass@host:5432/db"
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from kbquant.config import settings


# ─── 合法枚举值集合 ───────────────────────────────────────────────

VALID_NODE_TYPES = {
    "macro", "sector", "industry", "concept", "company",
    "product", "commodity", "currency", "region", "event",
    "policy", "index", "indicator", "person", "institution",
    "regulation",
}

VALID_INFO_TYPES = {
    "news", "report", "social_media", "filing", "research", "other",
}

VALID_PROCESSING_STATUS = {
    "ingested", "deduped", "entities_extracted", "attached_to_nodes",
    "analyzed", "world_model_updated", "trade_validated", "completed",
}

VALID_PREPROCESS_STATUS = {
    "ingested", "deduped", "entities_extracted", "attached_to_nodes",
    "analyzed", "world_model_updated", "trade_validated", "completed",
}

VALID_ANALYSIS_TYPES = {
    "impact_analysis", "driver_assessment", "risk_evaluation",
    "sentiment", "valuation", "technical", "macro",
}

VALID_TRADE_OP_TYPES = {
    "buy", "sell", "skip", "track", "stop_loss", "take_profit",
}

VALID_TRADE_STATUSES = {
    "pending", "executed", "cancelled", "expired",
}

VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}

VALID_ATTACHMENT_TYPES = {"raw_info", "analysis"}

VALID_ATTACHMENT_ROLES = {
    "primary", "secondary", "background", "risk",
    "historical_reference", "driver_evidence", "risk_evidence",
}

VALID_DEDUP_TYPES = {
    "exact_duplicate", "reprint", "follow_up", "same_event", "superseded",
}

VALID_CONFLICT_TYPES = {
    "contradiction", "refinement", "negation", "update",
}

VALID_ENTITY_TYPES = {
    "central_bank", "indicator", "currency", "region",
    "policy", "regulation", "industry_rule",
    "institution", "index",
    "company", "sector", "concept", "commodity", "product",
    "event", "person",
}

VALID_RELATIONSHIP_TYPES = {
    "impacts", "regulates", "sanctions", "holds", "part_of",
    "supplies", "produces", "competes_with", "substitutes",
    "cooperates_with", "correlated_with", "same_as",
}

VALID_WNE_RELATIONSHIP_TYPES = {
    "belongs_to", "classified_as", "operates_in", "has_business_segment",
    "derives_revenue_from", "upstream_of", "downstream_of",
    "competes_in", "threatens", "regulated_by", "benefits_from",
    "constrained_by", "affected_by", "driven_by", "based_in",
    "exposed_to", "led_by", "affiliated_with",
}

VALID_IE_ROLES = {"subject", "object", "context", "mentioned"}

VALID_TIME_HORIZONS = {"short_term", "medium_term", "long_term"}

VALID_IMPORTANCE_TARGET_TYPES = {
    "raw_info", "node", "analysis", "trade_candidate",
}

VALID_TIME_VALIDITY_TARGET_TYPES = {"driver", "risk", "focus_point"}


class Result:
    """单个检查项的结果"""
    def __init__(self, label: str, passed: bool, detail: str = ""):
        self.label = label
        self.passed = passed
        self.detail = detail


class CheckRegistry:
    """收集所有检查结果并汇总输出"""
    def __init__(self):
        self.results: list[Result] = []

    def add(self, label: str, passed: bool, detail: str = ""):
        self.results.append(Result(label, passed, detail))
        return passed

    def add_count(self, label: str, count: int, detail: str = ""):
        """count == 0 → passed"""
        passed = count == 0
        if not passed:
            self.add(label, False, f"发现 {count} 条异常记录" + (f": {detail}" if detail else ""))
        else:
            self.add(label, True, detail)
        return passed

    def summary(self):
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total = len(self.results)
        return passed, failed, total


# ─── 数据库连接 ────────────────────────────────────────────────────


def build_engine(url_override: str | None = None):
    url = url_override or settings.database_read_url or settings.database_url
    if not url:
        raise RuntimeError("未提供数据库连接 URL。请设置 DATABASE_URL 环境变量或通过 --db-url 指定。")

    # 异步脚本，直接用 asyncpg 直连（不走 pgbouncer 也不走 kbquant 的 pool 管理）
    if "asyncpg" not in url:
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)

    return create_async_engine(
        url,
        echo=False,
        connect_args={
            "server_settings": {"application_name": "kbquant_data_audit"},
        },
    )


# ─── 查询辅助 ───────────────────────────────────────────────────────


async def fetch_scalar(session: AsyncSession, query: str, params: dict | None = None) -> int:
    """执行 COUNT 查询并返回单个整数"""
    result = await session.execute(text(query), params or {})
    row = result.fetchone()
    return int(row[0]) if row else 0


async def fetch_rows(session: AsyncSession, query: str, params: dict | None = None, limit: int = 20) -> list:
    """执行查询并返回结果行列表（每行为 tuple）"""
    result = await session.execute(text(query), params or {})
    return result.fetchmany(limit)


# ─── 核心分析逻辑 ──────────────────────────────────────────────────


async def analyze_database(engine, verbose: bool = False):
    C = CheckRegistry()

    async with AsyncSession(engine) as session:

        # ═══════════════════════════════════════════════════════════
        # 1. NOT NULL 列检查
        # ═══════════════════════════════════════════════════════════

        print("=== 1. NOT NULL 约束检查 ===")

        null_checks = [
            ("world_nodes.name", "world_nodes", "name"),
            ("world_nodes.node_type", "world_nodes", "node_type"),
            ("world_nodes.is_active", "world_nodes", "is_active"),
            ("world_nodes.created_at", "world_nodes", "created_at"),
            ("world_nodes.updated_at", "world_nodes", "updated_at"),
            ("world_node_edges.parent_node_id", "world_node_edges", "parent_node_id"),
            ("world_node_edges.child_node_id", "world_node_edges", "child_node_id"),
            ("world_node_edges.relationship_type", "world_node_edges", "relationship_type"),
            ("world_node_edges.created_at", "world_node_edges", "created_at"),
            ("raw_information.title", "raw_information", "title"),
            ("raw_information.body", "raw_information", "body"),
            ("raw_information.source", "raw_information", "source"),
            ("raw_information.published_at", "raw_information", "published_at"),
            ("raw_information.ingested_at", "raw_information", "ingested_at"),
            ("raw_information.info_type", "raw_information", "info_type"),
            ("raw_information.language", "raw_information", "language"),
            ("raw_information.content_hash", "raw_information", "content_hash"),
            ("raw_information.processing_status", "raw_information", "processing_status"),
            ("raw_information.importance_score", "raw_information", "importance_score"),
            ("analyses.title", "analyses", "title"),
            ("analyses.content", "analyses", "content"),
            ("analyses.analysis_type", "analyses", "analysis_type"),
            ("analyses.created_at", "analyses", "created_at"),
            ("node_states.node_id", "node_states", "node_id"),
            ("node_states.version", "node_states", "version"),
            ("node_states.effective_from", "node_states", "effective_from"),
            ("node_states.created_at", "node_states", "created_at"),
            ("trading_operations.operation_type", "trading_operations", "operation_type"),
            ("trading_operations.status", "trading_operations", "status"),
            ("trading_operations.created_at", "trading_operations", "created_at"),
            ("feedbacks.title", "feedbacks", "title"),
            ("feedbacks.created_at", "feedbacks", "created_at"),
            ("entities.name", "entities", "name"),
            ("entities.entity_type", "entities", "entity_type"),
            ("entities.normalized_name", "entities", "normalized_name"),
            ("information_entities.raw_info_id", "information_entities", "raw_info_id"),
            ("information_entities.entity_id", "information_entities", "entity_id"),
            ("entity_relationships.source_entity_id", "entity_relationships", "source_entity_id"),
            ("entity_relationships.target_entity_id", "entity_relationships", "target_entity_id"),
            ("entity_relationships.relationship_type", "entity_relationships", "relationship_type"),
            ("node_attachments.node_id", "node_attachments", "node_id"),
            ("node_attachments.attachment_type", "node_attachments", "attachment_type"),
            ("node_attachments.attachment_id", "node_attachments", "attachment_id"),
            ("node_attachments.role", "node_attachments", "role"),
            ("information_dedups.primary_info_id", "information_dedups", "primary_info_id"),
            ("information_dedups.duplicate_info_id", "information_dedups", "duplicate_info_id"),
            ("information_dedups.dedup_type", "information_dedups", "dedup_type"),
            ("processing_queue.raw_info_id", "processing_queue", "raw_info_id"),
            ("processing_queue.status", "processing_queue", "status"),
            ("processing_queue.preprocess_status", "processing_queue", "preprocess_status"),
            ("processing_queue.priority", "processing_queue", "priority"),
            ("time_validities.target_type", "time_validities", "target_type"),
            ("time_validities.target_id", "time_validities", "target_id"),
            ("time_validities.valid_from", "time_validities", "valid_from"),
            ("time_validities.extended_count", "time_validities", "extended_count"),
            ("conflict_detections.node_id", "conflict_detections", "node_id"),
            ("conflict_detections.existing_claim", "conflict_detections", "existing_claim"),
            ("conflict_detections.conflicting_claim", "conflict_detections", "conflicting_claim"),
            ("conflict_detections.conflict_type", "conflict_detections", "conflict_type"),
            ("importance_rankings.target_type", "importance_rankings", "target_type"),
            ("importance_rankings.target_id", "importance_rankings", "target_id"),
            ("importance_rankings.importance_score", "importance_rankings", "importance_score"),
            ("importance_rankings.computed_at", "importance_rankings", "computed_at"),
            ("macro_reports.version", "macro_reports", "version"),
            ("macro_reports.content", "macro_reports", "content"),
            ("macro_reports.summary", "macro_reports", "summary"),
            ("structured_preferences.asset_preferences", "structured_preferences", "asset_preferences"),
            ("structured_preferences.risk_preferences", "structured_preferences", "risk_preferences"),
            ("structured_preferences.analysis_preferences", "structured_preferences", "analysis_preferences"),
            ("structured_preferences.learned_rules", "structured_preferences", "learned_rules"),
            ("industry_cognitions.sector", "industry_cognitions", "sector"),
            ("industry_cognitions.cognition_text", "industry_cognitions", "cognition_text"),
            ("industry_cognitions.append_count", "industry_cognitions", "append_count"),
            ("market_cognitions.cognition_text", "market_cognitions", "cognition_text"),
            ("market_cognitions.append_count", "market_cognitions", "append_count"),
        ]

        for label, table, col in null_checks:
            count = await fetch_scalar(session, f'SELECT COUNT(*) FROM {table} WHERE "{col}" IS NULL')
            C.add_count(label, count)
            if count > 0 and verbose:
                rows = await fetch_rows(session, f'SELECT id, "{col}" FROM {table} WHERE "{col}" IS NULL LIMIT 5')
                for r in rows:
                    print(f"    {table}.id={r[0]} {col}=NULL")

        # ═══════════════════════════════════════════════════════════
        # 2. 枚举值检查
        # ═══════════════════════════════════════════════════════════

        print("=== 2. 枚举值合法性检查 ===")

        enum_checks = [
            ("raw_information.info_type", "raw_information", "info_type", VALID_INFO_TYPES),
            ("raw_information.processing_status", "raw_information", "processing_status", VALID_PROCESSING_STATUS),
            ("raw_information.language (not empty)", "raw_information", "language", None),  # 特殊处理
            ("analyses.analysis_type", "analyses", "analysis_type", VALID_ANALYSIS_TYPES),
            ("analyses.time_horizon", "analyses", "time_horizon", VALID_TIME_HORIZONS),
            ("trading_operations.operation_type", "trading_operations", "operation_type", VALID_TRADE_OP_TYPES),
            ("trading_operations.status", "trading_operations", "status", VALID_TRADE_STATUSES),
            ("trading_operations.risk_level", "trading_operations", "risk_level", VALID_RISK_LEVELS),
            ("entities.entity_type", "entities", "entity_type", VALID_ENTITY_TYPES),
            ("entity_relationships.relationship_type", "entity_relationships", "relationship_type", VALID_RELATIONSHIP_TYPES),
            ("world_node_edges.relationship_type", "world_node_edges", "relationship_type", VALID_WNE_RELATIONSHIP_TYPES),
            ("node_attachments.attachment_type", "node_attachments", "attachment_type", VALID_ATTACHMENT_TYPES),
            ("node_attachments.role", "node_attachments", "role", VALID_ATTACHMENT_ROLES),
            ("information_dedups.dedup_type", "information_dedups", "dedup_type", VALID_DEDUP_TYPES),
            ("information_entities.role", "information_entities", "role", VALID_IE_ROLES),
            ("conflict_detections.conflict_type", "conflict_detections", "conflict_type", VALID_CONFLICT_TYPES),
            ("processing_queue.status", "processing_queue", "status", VALID_PROCESSING_STATUS),
            ("processing_queue.preprocess_status", "processing_queue", "preprocess_status", VALID_PREPROCESS_STATUS),
            ("importance_rankings.target_type", "importance_rankings", "target_type", VALID_IMPORTANCE_TARGET_TYPES),
            ("time_validities.target_type", "time_validities", "target_type", VALID_TIME_VALIDITY_TARGET_TYPES),
        ]

        for label, table, col, valid_set in enum_checks:
            if valid_set is None:
                continue  # 跳过特殊处理的

            placeholders = ", ".join(f"'{v}'" for v in valid_set)
            query = f'SELECT COUNT(*) FROM {table} WHERE "{col}" IS NOT NULL AND "{col}" NOT IN ({placeholders})'
            total = await fetch_scalar(session, query)
            C.add_count(label, total)
            if total > 0 and verbose:
                rows = await fetch_rows(
                    session,
                    f'SELECT id, "{col}" FROM {table} WHERE "{col}" IS NOT NULL AND "{col}" NOT IN ({placeholders}) LIMIT 10',
                )
                for r in rows:
                    print(f"    {table}.id={r[0]} {col}={r[1]!r}")

        # 特殊：raw_information.language 不能为空
        empty_lang = await fetch_scalar(session, "SELECT COUNT(*) FROM raw_information WHERE language = '' OR language IS NULL")
        C.add_count("raw_information.language (非空)", empty_lang)

        # world_nodes.node_type
        wn_placeholders = ", ".join(f"'{v}'" for v in VALID_NODE_TYPES)
        bad_wn_type = await fetch_scalar(session, f"SELECT COUNT(*) FROM world_nodes WHERE node_type NOT IN ({wn_placeholders})")
        C.add_count("world_nodes.node_type", bad_wn_type)

        # ═══════════════════════════════════════════════════════════
        # 3. 外键引用完整性（孤儿记录）
        # ═══════════════════════════════════════════════════════════

        print("=== 3. 外键引用完整性（孤儿记录）===")

        # 3a. world_node_edges → world_nodes
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM world_node_edges e
            LEFT JOIN world_nodes p ON e.parent_node_id = p.id
            LEFT JOIN world_nodes c ON e.child_node_id = c.id
            WHERE p.id IS NULL OR c.id IS NULL
        """)
        C.add_count("world_node_edges → world_nodes (parent/child)", orphan)

        if orphan > 0 and verbose:
            rows = await fetch_rows(session, """
                SELECT e.id, e.parent_node_id, e.child_node_id,
                       (p.id IS NULL) AS missing_parent,
                       (c.id IS NULL) AS missing_child
                FROM world_node_edges e
                LEFT JOIN world_nodes p ON e.parent_node_id = p.id
                LEFT JOIN world_nodes c ON e.child_node_id = c.id
                WHERE p.id IS NULL OR c.id IS NULL LIMIT 10
            """)
            for r in rows:
                print(f"    edge={r[0]} parent={r[1]} missing_parent={r[3]} child={r[2]} missing_child={r[4]}")

        # 3b. world_node_edges evidence_ids → raw_information
        # (ARRAY 列中的每个 UUID 是否存在于 raw_information)
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT UNNEST(evidence_ids) AS eid FROM world_node_edges WHERE evidence_ids IS NOT NULL
            ) t
            LEFT JOIN raw_information ri ON t.eid = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("world_node_edges.evidence_ids → raw_information", orphan)

        # 3c. node_states → world_nodes
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM node_states ns
            LEFT JOIN world_nodes wn ON ns.node_id = wn.id
            WHERE wn.id IS NULL
        """)
        C.add_count("node_states → world_nodes", orphan)

        # 3d. node_states.key_evidence_ids → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT UNNEST(key_evidence_ids) AS eid FROM node_states WHERE key_evidence_ids IS NOT NULL
            ) t
            LEFT JOIN raw_information ri ON t.eid = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("node_states.key_evidence_ids → raw_information", orphan)

        # 3e. analyses.parent_analysis_id → analyses (自引用)
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM analyses a
            LEFT JOIN analyses parent ON a.parent_analysis_id = parent.id
            WHERE a.parent_analysis_id IS NOT NULL AND parent.id IS NULL
        """)
        C.add_count("analyses.parent_analysis_id → analyses", orphan)

        # 3f. analyses.root_raw_info_ids → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT UNNEST(root_raw_info_ids) AS rid FROM analyses WHERE root_raw_info_ids IS NOT NULL
            ) t
            LEFT JOIN raw_information ri ON t.rid = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("analyses.root_raw_info_ids → raw_information", orphan)

        # 3g. trading_operations.target_node_id → world_nodes
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM trading_operations t
            LEFT JOIN world_nodes wn ON t.target_node_id = wn.id
            WHERE t.target_node_id IS NOT NULL AND wn.id IS NULL
        """)
        C.add_count("trading_operations.target_node_id → world_nodes", orphan)

        # 3h. trading_operations.trigger_analysis_id → analyses
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM trading_operations t
            LEFT JOIN analyses a ON t.trigger_analysis_id = a.id
            WHERE t.trigger_analysis_id IS NOT NULL AND a.id IS NULL
        """)
        C.add_count("trading_operations.trigger_analysis_id → analyses", orphan)

        # 3i. trading_operations.parent_operation_id → trading_operations (自引用)
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM trading_operations t
            LEFT JOIN trading_operations parent ON t.parent_operation_id = parent.id
            WHERE t.parent_operation_id IS NOT NULL AND parent.id IS NULL
        """)
        C.add_count("trading_operations.parent_operation_id → trading_operations", orphan)

        # 3j. trading_operations.trigger_raw_ids → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT UNNEST(trigger_raw_ids) AS rid FROM trading_operations WHERE trigger_raw_ids IS NOT NULL
            ) t
            LEFT JOIN raw_information ri ON t.rid = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("trading_operations.trigger_raw_ids → raw_information", orphan)

        # 3k. feedbacks.trigger_analysis_id → analyses
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM feedbacks f
            LEFT JOIN analyses a ON f.trigger_analysis_id = a.id
            WHERE f.trigger_analysis_id IS NOT NULL AND a.id IS NULL
        """)
        C.add_count("feedbacks.trigger_analysis_id → analyses", orphan)

        # 3l. feedbacks.trigger_trade_id → trading_operations
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM feedbacks f
            LEFT JOIN trading_operations t ON f.trigger_trade_id = t.id
            WHERE f.trigger_trade_id IS NOT NULL AND t.id IS NULL
        """)
        C.add_count("feedbacks.trigger_trade_id → trading_operations", orphan)

        # 3m. entities.linked_node_id → world_nodes
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM entities e
            LEFT JOIN world_nodes wn ON e.linked_node_id = wn.id
            WHERE e.linked_node_id IS NOT NULL AND wn.id IS NULL
        """)
        C.add_count("entities.linked_node_id → world_nodes", orphan)

        # 3n. information_entities.raw_info_id → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM information_entities ie
            LEFT JOIN raw_information ri ON ie.raw_info_id = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("information_entities.raw_info_id → raw_information", orphan)

        # 3o. information_entities.entity_id → entities
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM information_entities ie
            LEFT JOIN entities e ON ie.entity_id = e.id
            WHERE e.id IS NULL
        """)
        C.add_count("information_entities.entity_id → entities", orphan)

        # 3p. entity_relationships.source_entity_id → entities
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM entity_relationships er
            LEFT JOIN entities src ON er.source_entity_id = src.id
            WHERE src.id IS NULL
        """)
        C.add_count("entity_relationships.source_entity_id → entities", orphan)

        # 3q. entity_relationships.target_entity_id → entities
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM entity_relationships er
            LEFT JOIN entities tgt ON er.target_entity_id = tgt.id
            WHERE tgt.id IS NULL
        """)
        C.add_count("entity_relationships.target_entity_id → entities", orphan)

        # 3r. entity_relationships.evidence_info_ids → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT UNNEST(evidence_info_ids) AS eid FROM entity_relationships WHERE evidence_info_ids IS NOT NULL
            ) t
            LEFT JOIN raw_information ri ON t.eid = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("entity_relationships.evidence_info_ids → raw_information", orphan)

        # 3s. node_attachments.node_id → world_nodes
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM node_attachments na
            LEFT JOIN world_nodes wn ON na.node_id = wn.id
            WHERE wn.id IS NULL
        """)
        C.add_count("node_attachments.node_id → world_nodes", orphan)

        # 3t. information_dedups.primary_info_id → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM information_dedups d
            LEFT JOIN raw_information ri ON d.primary_info_id = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("information_dedups.primary_info_id → raw_information", orphan)

        # 3u. information_dedups.duplicate_info_id → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM information_dedups d
            LEFT JOIN raw_information ri ON d.duplicate_info_id = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("information_dedups.duplicate_info_id → raw_information", orphan)

        # 3v. processing_queue.raw_info_id → raw_information (1:1)
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM processing_queue pq
            LEFT JOIN raw_information ri ON pq.raw_info_id = ri.id
            WHERE ri.id IS NULL
        """)
        C.add_count("processing_queue.raw_info_id → raw_information", orphan)

        # 3w. conflict_detections.node_id → world_nodes
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM conflict_detections cd
            LEFT JOIN world_nodes wn ON cd.node_id = wn.id
            WHERE wn.id IS NULL
        """)
        C.add_count("conflict_detections.node_id → world_nodes", orphan)

        # 3x. conflict_detections.existing_evidence_id → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM conflict_detections cd
            LEFT JOIN raw_information ri ON cd.existing_evidence_id = ri.id
            WHERE cd.existing_evidence_id IS NOT NULL AND ri.id IS NULL
        """)
        C.add_count("conflict_detections.existing_evidence_id → raw_information", orphan)

        # 3y. conflict_detections.conflicting_evidence_id → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM conflict_detections cd
            LEFT JOIN raw_information ri ON cd.conflicting_evidence_id = ri.id
            WHERE cd.conflicting_evidence_id IS NOT NULL AND ri.id IS NULL
        """)
        C.add_count("conflict_detections.conflicting_evidence_id → raw_information", orphan)

        # ═══════════════════════════════════════════════════════════
        # 4. 多态外键完整性
        # ═══════════════════════════════════════════════════════════

        print("=== 4. 多态外键完整性 ===")

        # 4a. node_attachments: attachment_type='raw_info' → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM node_attachments na
            LEFT JOIN raw_information ri ON na.attachment_id = ri.id
            WHERE na.attachment_type = 'raw_info' AND ri.id IS NULL
        """)
        C.add_count("node_attachments (raw_info) → raw_information", orphan)

        # 4b. node_attachments: attachment_type='analysis' → analyses
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM node_attachments na
            LEFT JOIN analyses a ON na.attachment_id = a.id
            WHERE na.attachment_type = 'analysis' AND a.id IS NULL
        """)
        C.add_count("node_attachments (analysis) → analyses", orphan)

        # 4c. importance_rankings: target_type='raw_info' → raw_information
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM importance_rankings ir
            LEFT JOIN raw_information ri ON ir.target_id = ri.id
            WHERE ir.target_type = 'raw_info' AND ri.id IS NULL
        """)
        C.add_count("importance_rankings (raw_info) → raw_information", orphan)

        # 4d. importance_rankings: target_type='node' → world_nodes
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM importance_rankings ir
            LEFT JOIN world_nodes wn ON ir.target_id = wn.id
            WHERE ir.target_type = 'node' AND wn.id IS NULL
        """)
        C.add_count("importance_rankings (node) → world_nodes", orphan)

        # 4e. importance_rankings: target_type='analysis' → analyses
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM importance_rankings ir
            LEFT JOIN analyses a ON ir.target_id = a.id
            WHERE ir.target_type = 'analysis' AND a.id IS NULL
        """)
        C.add_count("importance_rankings (analysis) → analyses", orphan)

        # 4f. importance_rankings: target_type='trade_candidate' → trading_operations
        orphan = await fetch_scalar(session, """
            SELECT COUNT(*) FROM importance_rankings ir
            LEFT JOIN trading_operations t ON ir.target_id = t.id
            WHERE ir.target_type = 'trade_candidate' AND t.id IS NULL
        """)
        C.add_count("importance_rankings (trade_candidate) → trading_operations", orphan)

        # ═══════════════════════════════════════════════════════════
        # 5. 逻辑矛盾检查
        # ═══════════════════════════════════════════════════════════

        print("=== 5. 逻辑矛盾检查 ===")

        # 5a. world_node_edges: parent_node_id == child_node_id (自环)
        self_loop = await fetch_scalar(session, """
            SELECT COUNT(*) FROM world_node_edges
            WHERE parent_node_id = child_node_id
        """)
        C.add_count("world_node_edges: 自环 (parent=child)", self_loop)
        if self_loop > 0 and verbose:
            rows = await fetch_rows(session, """
                SELECT id, parent_node_id, child_node_id, relationship_type
                FROM world_node_edges WHERE parent_node_id = child_node_id LIMIT 10
            """)
            for r in rows:
                print(f"    edge={r[0]} node={r[1]} type={r[3]}")

        # 5b. entity_relationships: source == target (自环)
        self_loop = await fetch_scalar(session, """
            SELECT COUNT(*) FROM entity_relationships
            WHERE source_entity_id = target_entity_id
        """)
        C.add_count("entity_relationships: 自环 (source=target)", self_loop)

        # 5c. analyses: parent_analysis_id == id (自引用)
        self_ref = await fetch_scalar(session, """
            SELECT COUNT(*) FROM analyses WHERE parent_analysis_id = id
        """)
        C.add_count("analyses: 自引用 (parent_analysis_id=id)", self_ref)

        # 5d. information_dedups: primary_info_id == duplicate_info_id
        self_ref = await fetch_scalar(session, """
            SELECT COUNT(*) FROM information_dedups WHERE primary_info_id = duplicate_info_id
        """)
        C.add_count("information_dedups: primary=duplicate", self_ref)

        # 5e. trading_operations: parent_operation_id == id
        self_ref = await fetch_scalar(session, """
            SELECT COUNT(*) FROM trading_operations WHERE parent_operation_id = id
        """)
        C.add_count("trading_operations: 自引用 (parent_operation_id=id)", self_ref)

        # 5f. raw_information: published_at > ingested_at (发布时间晚于收录时间 — 可以容忍但打 warning)
        future_pub = await fetch_scalar(session, """
            SELECT COUNT(*) FROM raw_information WHERE published_at > ingested_at
        """)
        if future_pub > 0:
            C.add(f"raw_information: published_at > ingested_at", False, f"{future_pub} 条")
        else:
            C.add("raw_information: published_at > ingested_at", True)

        # 5g. node_states: effective_from > effective_to (时间倒挂)
        time_inv = await fetch_scalar(session, """
            SELECT COUNT(*) FROM node_states
            WHERE effective_to IS NOT NULL AND effective_from > effective_to
        """)
        C.add_count("node_states: effective_from > effective_to", time_inv)
        if time_inv > 0 and verbose:
            rows = await fetch_rows(session, """
                SELECT id, node_id, version, effective_from, effective_to
                FROM node_states
                WHERE effective_to IS NOT NULL AND effective_from > effective_to LIMIT 10
            """)
            for r in rows:
                print(f"    ns={r[0]} node={r[1]} v={r[2]} from={r[3]} to={r[4]}")

        # 5h. processing_queue: started_at > completed_at
        time_inv = await fetch_scalar(session, """
            SELECT COUNT(*) FROM processing_queue
            WHERE started_at IS NOT NULL AND completed_at IS NOT NULL
              AND started_at > completed_at
        """)
        C.add_count("processing_queue: started_at > completed_at", time_inv)

        # 5i. analyses confidence 范围 [0, 1]
        bad_conf = await fetch_scalar(session, """
            SELECT COUNT(*) FROM analyses
            WHERE confidence IS NOT NULL AND (confidence < 0 OR confidence > 1)
        """)
        C.add_count("analyses.confidence ∉ [0,1]", bad_conf)
        if bad_conf > 0 and verbose:
            rows = await fetch_rows(session, """
                SELECT id, confidence FROM analyses
                WHERE confidence IS NOT NULL AND (confidence < 0 OR confidence > 1) LIMIT 10
            """)
            for r in rows:
                print(f"    analysis={r[0]} confidence={r[1]}")

        # 5j. node_attachments relevance_score 范围 [0, 1]
        bad_score = await fetch_scalar(session, """
            SELECT COUNT(*) FROM node_attachments
            WHERE relevance_score IS NOT NULL AND (relevance_score < 0 OR relevance_score > 1)
        """)
        C.add_count("node_attachments.relevance_score ∉ [0,1]", bad_score)

        # 5k. information_entities relevance_score 范围 [0, 1]
        bad_score = await fetch_scalar(session, """
            SELECT COUNT(*) FROM information_entities
            WHERE relevance_score IS NOT NULL AND (relevance_score < 0 OR relevance_score > 1)
        """)
        C.add_count("information_entities.relevance_score ∉ [0,1]", bad_score)

        # 5l. information_entities extraction_confidence 范围 [0, 1]
        bad_score = await fetch_scalar(session, """
            SELECT COUNT(*) FROM information_entities
            WHERE extraction_confidence IS NOT NULL AND (extraction_confidence < 0 OR extraction_confidence > 1)
        """)
        C.add_count("information_entities.extraction_confidence ∉ [0,1]", bad_score)

        # 5m. importance_rankings importance_score 范围 [0, 1]
        bad_score = await fetch_scalar(session, """
            SELECT COUNT(*) FROM importance_rankings
            WHERE importance_score < 0 OR importance_score > 1
        """)
        C.add_count("importance_rankings.importance_score ∉ [0,1]", bad_score)

        # 5n. entity_relationships strength 范围 [0, 1]
        bad_str = await fetch_scalar(session, """
            SELECT COUNT(*) FROM entity_relationships
            WHERE strength IS NOT NULL AND (strength < 0 OR strength > 1)
        """)
        C.add_count("entity_relationships.strength ∉ [0,1]", bad_str)

        # 5o. world_node_edges weight <= 0
        bad_wt = await fetch_scalar(session, """
            SELECT COUNT(*) FROM world_node_edges WHERE weight <= 0
        """)
        C.add_count("world_node_edges.weight <= 0", bad_wt)

        # 5p. raw_information importance_score < 0
        bad_score = await fetch_scalar(session, """
            SELECT COUNT(*) FROM raw_information WHERE importance_score < 0
        """)
        C.add_count("raw_information.importance_score < 0", bad_score)

        # 5q. feedbacks: trigger_analysis_id 和 trigger_trade_id 同时为 NULL
        both_null = await fetch_scalar(session, """
            SELECT COUNT(*) FROM feedbacks
            WHERE trigger_analysis_id IS NULL AND trigger_trade_id IS NULL
        """)
        C.add_count("feedbacks: 无关联分析也无关联交易", both_null)

        # 5r. conflict_detections: resolved_at < created_at
        bad_resolve = await fetch_scalar(session, """
            SELECT COUNT(*) FROM conflict_detections
            WHERE resolved_at IS NOT NULL AND resolved_at < created_at
        """)
        C.add_count("conflict_detections: resolved_at < created_at", bad_resolve)

        # ═══════════════════════════════════════════════════════════
        # 6. 管线一致性
        # ═══════════════════════════════════════════════════════════

        print("=== 6. 管线一致性检查 ===")

        # 6a. raw_information 无对应 processing_queue (1:1)
        no_queue = await fetch_scalar(session, """
            SELECT COUNT(*)
            FROM raw_information ri
            LEFT JOIN processing_queue pq ON ri.id = pq.raw_info_id
            WHERE pq.id IS NULL
        """)
        C.add_count("raw_information 缺失 processing_queue", no_queue)

        # 6b. processing_queue 无对应 raw_information (已在 3v 检查)

        # 6c. raw_information status='completed' 但没有 analyses
        completed_no_analysis = await fetch_scalar(session, """
            SELECT COUNT(*)
            FROM raw_information ri
            WHERE ri.processing_status = 'completed'
              AND NOT EXISTS (SELECT 1 FROM analyses a WHERE ri.id = ANY(a.root_raw_info_ids))
        """)
        C.add_count("raw_information completed 但无关联 analyses", completed_no_analysis)

        # 6d. analyses 引用的 root_raw_info_ids 指向的 raw_info status 不是 completed
        not_completed = await fetch_scalar(session, """
            SELECT COUNT(DISTINCT t.rid)
            FROM (
                SELECT UNNEST(root_raw_info_ids) AS rid FROM analyses WHERE root_raw_info_ids IS NOT NULL
            ) t
            JOIN raw_information ri ON t.rid = ri.id
            WHERE ri.processing_status != 'completed'
        """)
        C.add_count("analyses 引用了未 completed 的 raw_information", not_completed)

        # 6e. processing_queue 状态与 raw_information 状态不一致
        mismatch = await fetch_scalar(session, """
            SELECT COUNT(*)
            FROM processing_queue pq
            JOIN raw_information ri ON pq.raw_info_id = ri.id
            WHERE pq.status != ri.processing_status
        """)
        C.add_count("processing_queue.status ≠ raw_information.processing_status", mismatch)

        # 6f. trading_operations status='executed' 但 executed_at IS NULL
        executed_no_time = await fetch_scalar(session, """
            SELECT COUNT(*) FROM trading_operations
            WHERE status = 'executed' AND executed_at IS NULL
        """)
        C.add_count("trading_operations executed 但无 executed_at", executed_no_time)

        # 6g. trading_operations status='pending' 但 executed_at 有值
        pending_with_time = await fetch_scalar(session, """
            SELECT COUNT(*) FROM trading_operations
            WHERE status = 'pending' AND executed_at IS NOT NULL
        """)
        C.add_count("trading_operations pending 但有 executed_at", pending_with_time)

        # ═══════════════════════════════════════════════════════════
        # 7. 数据质量检查
        # ═══════════════════════════════════════════════════════════

        print("=== 7. 数据质量检查 ===")

        # 7a. entities: normalized_name 是否真的是规范化的（全小写、无前后空格）
        not_normalized = await fetch_scalar(session, """
            SELECT COUNT(*) FROM entities
            WHERE normalized_name != LOWER(TRIM(normalized_name))
        """)
        C.add_count("entities: normalized_name 未规范化", not_normalized)
        if not_normalized > 0 and verbose:
            rows = await fetch_rows(session, """
                SELECT id, name, normalized_name FROM entities
                WHERE normalized_name != LOWER(TRIM(normalized_name)) LIMIT 10
            """)
            for r in rows:
                print(f"    entity={r[0]} name={r[1]} normalized={r[2]!r}")

        # 7b. world_nodes: 空别名字符串
        empty_aliases = await fetch_scalar(session, """
            SELECT COUNT(*) FROM world_nodes
            WHERE aliases IS NOT NULL AND '' = ANY(aliases)
        """)
        C.add_count("world_nodes: aliases 含空字符串", empty_aliases)

        # 7c. world_nodes: 名称含前后空格
        name_ws = await fetch_scalar(session, """
            SELECT COUNT(*) FROM world_nodes WHERE name != TRIM(name)
        """)
        C.add_count("world_nodes: name 含前后空格", name_ws)

        # 7d. raw_information: title 或 body 为空字符串
        empty_content = await fetch_scalar(session, """
            SELECT COUNT(*) FROM raw_information WHERE title = '' OR body = ''
        """)
        C.add_count("raw_information: title 或 body 为空字符串", empty_content)

        # 7e. analyses: content 为空字符串
        empty_content = await fetch_scalar(session, """
            SELECT COUNT(*) FROM analyses WHERE content = '' OR title = ''
        """)
        C.add_count("analyses: title 或 content 为空字符串", empty_content)

        # 7f. macro_reports: summary 为空字符串 (NOT NULL default '')
        empty_summary = await fetch_scalar(session, """
            SELECT COUNT(*) FROM macro_reports WHERE summary = '' AND content != ''
        """)
        C.add_count("macro_reports: summary 为空但有 content", empty_summary)

        # 7g. node_states: 同一 node_id 下存在重复的 version
        dup_ver = await fetch_scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT node_id, version, COUNT(*) AS cnt
                FROM node_states GROUP BY node_id, version HAVING COUNT(*) > 1
            ) t
        """)
        C.add_count("node_states: (node_id, version) 不唯一", dup_ver)

        # 7h. node_states: version 中断 (gap check)
        gaps = await fetch_scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT node_id, version,
                       LEAD(version) OVER (PARTITION BY node_id ORDER BY version) AS next_ver
                FROM node_states
            ) t
            WHERE next_ver IS NOT NULL AND next_ver != version + 1
        """)
        C.add_count("node_states: version 编号不连续", gaps)
        if gaps > 0 and verbose:
            rows = await fetch_rows(session, """
                SELECT node_id, version, next_ver
                FROM (
                    SELECT node_id, version,
                           LEAD(version) OVER (PARTITION BY node_id ORDER BY version) AS next_ver
                    FROM node_states
                ) t
                WHERE next_ver IS NOT NULL AND next_ver != version + 1 LIMIT 10
            """)
            for r in rows:
                print(f"    node={r[0]} version={r[1]} next={r[2]}")

        # 7i. trading_operations: symbol 与 world_nodes.ticker 不一致 (当有 target_node_id 时)
        mismatch = await fetch_scalar(session, """
            SELECT COUNT(*)
            FROM trading_operations t
            JOIN world_nodes wn ON t.target_node_id = wn.id
            WHERE t.symbol IS NOT NULL AND wn.ticker IS NOT NULL AND t.symbol != wn.ticker
        """)
        C.add_count("trading_operations.symbol ≠ world_nodes.ticker", mismatch)
        if mismatch > 0 and verbose:
            rows = await fetch_rows(session, """
                SELECT t.id, t.symbol, t.target_node_id, wn.ticker
                FROM trading_operations t
                JOIN world_nodes wn ON t.target_node_id = wn.id
                WHERE t.symbol IS NOT NULL AND wn.ticker IS NOT NULL AND t.symbol != wn.ticker LIMIT 10
            """)
            for r in rows:
                print(f"    trade={r[0]} symbol={r[1]} node={r[2]} ticker={r[3]}")

        # 7j. raw_information: content_hash == '' 或为 NULL
        bad_hash = await fetch_scalar(session, """
            SELECT COUNT(*) FROM raw_information WHERE content_hash = '' OR content_hash IS NULL
        """)
        C.add_count("raw_information: content_hash 为空", bad_hash)

        # 7k. world_nodes ticker 含空格
        bad_ticker = await fetch_scalar(session, """
            SELECT COUNT(*) FROM world_nodes
            WHERE ticker IS NOT NULL AND ticker != TRIM(ticker)
        """)
        C.add_count("world_nodes: ticker 含空格", bad_ticker)

        # ═══════════════════════════════════════════════════════════
        # 8. 基础统计概览
        # ═══════════════════════════════════════════════════════════

        print("=== 8. 数据概览 ===")

        tables = [
            "world_nodes", "world_node_edges", "raw_information", "analyses",
            "node_states", "trading_operations", "feedbacks", "entities",
            "information_entities", "entity_relationships", "node_attachments",
            "information_dedups", "processing_queue", "time_validities",
            "conflict_detections", "importance_rankings", "macro_reports",
            "structured_preferences", "industry_cognitions", "market_cognitions",
        ]

        print(f"\n{'表名':<30} {'行数':>8}")
        print("-" * 40)
        for table in tables:
            try:
                count = await fetch_scalar(session, f"SELECT COUNT(*) FROM {table}")
                print(f"{table:<30} {count:>8,}")
            except Exception as e:
                print(f"{table:<30} {'ERR':>8}  ({e})")

    return C


# ─── 入口 ───────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="kbquant 数据库非法数据分析")
    parser.add_argument("--db-url", type=str, default=None, help="数据库连接 URL")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出所有检查明细")
    parser.add_argument("--quiet", "-q", action="store_true", help="仅输出失败项")
    args = parser.parse_args()

    engine = build_engine(args.db_url)

    try:
        C = await analyze_database(engine, verbose=args.verbose)
    finally:
        await engine.dispose()

    passed, failed, total = C.summary()

    # ─── 输出汇总 ───
    print("\n" + "=" * 70)
    print("分析汇总")
    print("=" * 70)

    if not args.quiet:
        for r in C.results:
            if r.passed and args.verbose:
                print(f"  ✅ {r.label}")
            elif not r.passed:
                print(f"  ❌ {r.label}: {r.detail}")

    if not args.verbose and not args.quiet:
        # 非 verbose 模式：仅列出失败的
        failures = [r for r in C.results if not r.passed]
        if failures:
            print("\n异常项:")
            for r in failures:
                print(f"  ❌ {r.label}: {r.detail}")
        else:
            print("未发现异常数据。")

    print(f"\n通过: {passed} / 失败: {failed} / 总计: {total}")
    if failed > 0:
        print("⚠️  数据库存在非法/异常数据，请检查上述失败项。")
        return 1
    else:
        print("✅ 数据库未发现明显非法数据。")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
