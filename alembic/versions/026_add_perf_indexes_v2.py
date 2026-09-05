"""Add performance indexes for query optimization

Revision ID: 026
Revises: 025
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 实体关系复合索引 — 加速 upsert_relationship 和 upsert_relationships_many 的查找
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_er_source_target_type "
        "ON entity_relationships (source_entity_id, target_entity_id, relationship_type)"
    )

    # 2. WorldNode 名称三元组索引 — 加速 ilike '%keyword%' 模糊搜索
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_world_nodes_name_trgm "
        "ON world_nodes USING gin (name gin_trgm_ops)"
    )

    # 3. macro_reports version 索引 — 加速 ORDER BY version DESC LIMIT 1
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_macro_reports_version "
        "ON macro_reports (version DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_macro_reports_version")
    op.execute("DROP INDEX IF EXISTS ix_world_nodes_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_er_source_target_type")
