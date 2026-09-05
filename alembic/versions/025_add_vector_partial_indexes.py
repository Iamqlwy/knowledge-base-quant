"""Add partial indexes for vector search optimization

Revision ID: 025
Revises: 024
Create Date: 2026-06-09
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PARTIAL_INDEXES = [
    ("raw_information", "idx_raw_info_embedding_not_null"),
    ("analyses", "idx_analyses_embedding_not_null"),
    ("node_states", "idx_node_states_embedding_not_null"),
]


def upgrade() -> None:
    """创建部分索引以优化向量搜索性能

    注意：由于 CONCURRENTLY 不能在事务中运行，这些索引需要手动创建
    或者使用非 CONCURRENTLY 方式（会锁表但在小表上影响不大）
    """
    conn = op.get_bind()

    for table, index_name in PARTIAL_INDEXES:
        # 先检查索引是否已存在
        result = conn.execute(text(
            f"SELECT 1 FROM pg_indexes WHERE indexname = '{index_name}'"
        ))
        if result.fetchone():
            continue

        # 使用非 CONCURRENTLY 方式创建索引（在迁移事务中可以运行）
        # 对于小到中等规模的表，锁表时间很短
        op.execute(
            f"CREATE INDEX {index_name} "
            f"ON {table} (id) WHERE embedding IS NOT NULL"
        )


def downgrade() -> None:
    """删除部分索引"""
    for _, index_name in PARTIAL_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
