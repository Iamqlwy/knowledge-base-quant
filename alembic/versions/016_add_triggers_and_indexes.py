"""Add tsvector triggers and HNSW indexes

Revision ID: 016
Revises: 015
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION update_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('simple',
        COALESCE(NEW.title, '') || ' ' ||
        COALESCE(NEW.body, '') || ' ' ||
        COALESCE(NEW.content, '') || ' ' ||
        COALESCE(NEW.name, '') || ' ' ||
        COALESCE(NEW.state_summary, '') || ' ' ||
        COALESCE(NEW.core_logic, '') || ' ' ||
        COALESCE(NEW.lessons_learned, '') || ' ' ||
        COALESCE(NEW.rationale, '') || ' ' ||
        COALESCE(NEW.description, '')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGERS = [
    ("raw_information",),
    ("analyses",),
    ("feedbacks",),
    ("world_nodes",),
    ("node_states",),
]

HNSW_INDEXES = [
    ("raw_information", "ix_raw_info_hnsw"),
    ("analyses", "ix_analyses_hnsw"),
    ("node_states", "ix_node_states_hnsw"),
]


def upgrade() -> None:
    op.execute(TRIGGER_FUNCTION)
    for (table,) in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER trg_{table}_search_vector "
            f"BEFORE INSERT OR UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION update_search_vector()"
        )
    for table, index_name in HNSW_INDEXES:
        op.execute(
            f"CREATE INDEX {index_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = 16, ef_construction = 200)"
        )


def downgrade() -> None:
    for table, index_name in HNSW_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    for (table,) in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_search_vector ON {table}")
    op.execute("DROP FUNCTION IF EXISTS update_search_vector()")
