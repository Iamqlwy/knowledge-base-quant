"""Drop search_vector columns, indexes, and triggers from all tables

Revision ID: 018
Revises: 017
Create Date: 2026-05-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "raw_information",
    "analyses",
    "feedbacks",
    "world_nodes",
    "node_states",
]

PER_TABLE_COLUMNS = {
    "raw_information": ["title", "body"],
    "analyses": ["title", "content"],
    "feedbacks": ["title", "lessons_learned"],
    "world_nodes": ["name", "description"],
    "node_states": ["core_logic", "state_summary"],
}


def _make_function(table: str, columns: list[str]) -> str:
    coalesce_parts = " || ' ' ||\n        ".join(f"COALESCE(NEW.{c}, '')" for c in columns)
    return f"""
CREATE OR REPLACE FUNCTION tsvector_{table}() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('simple',
        {coalesce_parts} || ' '
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_search_vector ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS tsvector_{table}()")
        op.drop_index(f"ix_{table}_search", table_name=table, if_exists=True)
        op.drop_column(table, "search_vector")


def downgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("search_vector", sa.TEXT(), nullable=True))
        op.create_index(f"ix_{table}_search", table, ["search_vector"], postgresql_using="gin")

    for table, columns in PER_TABLE_COLUMNS.items():
        op.execute(_make_function(table, columns))
        op.execute(
            f"CREATE TRIGGER trg_{table}_search_vector "
            f"BEFORE INSERT OR UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION tsvector_{table}()"
        )
