"""Change embedding vector columns from 1024 to 1536 with HNSW indexes.

Revision ID: 1819b1b1eb25
Revises: 027
Create Date: 2026-06-18 20:46:45.189301
"""
from typing import Sequence, Union

from alembic import op

revision: str = '1819b1b1eb25'
down_revision: Union[str, None] = '027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ["raw_information", "analyses", "node_states", "feedbacks"]

OLD_HNSW_INDEXES = [
    ("raw_information", "ix_raw_info_hnsw"),
    ("analyses", "ix_analyses_hnsw"),
    ("node_states", "ix_node_states_hnsw"),
]

NEW_HNSW_INDEXES = [
    ("raw_information", "ix_raw_info_hnsw"),
    ("analyses", "ix_analyses_hnsw"),
    ("node_states", "ix_node_states_hnsw"),
    ("feedbacks", "ix_feedbacks_hnsw"),
]


def upgrade() -> None:
    # Drop old HNSW indexes (may exist from migration 016)
    for table, idx in OLD_HNSW_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {idx}")

    # Alter column types to 1536
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(1536)"
        )

    # Create HNSW indexes (1536 is within the 2000-dim limit)
    for table, idx in NEW_HNSW_INDEXES:
        op.execute(
            f"CREATE INDEX {idx} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = 16, ef_construction = 200)"
        )


def downgrade() -> None:
    for table, idx in NEW_HNSW_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {idx}")

    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(1024)"
        )

    for table, idx in OLD_HNSW_INDEXES:
        op.execute(
            f"CREATE INDEX {idx} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = 16, ef_construction = 200)"
        )
