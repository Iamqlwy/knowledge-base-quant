"""Fix search vector triggers - use per-table functions instead of shared function

Revision ID: 017
Revises: 016
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PER_TABLE_TRIGGERS = {
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
    # Drop old shared trigger and function
    for table in PER_TABLE_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_search_vector ON {table}")
    op.execute("DROP FUNCTION IF EXISTS update_search_vector()")

    # Create per-table trigger functions and triggers
    for table, columns in PER_TABLE_TRIGGERS.items():
        op.execute(_make_function(table, columns))
        op.execute(
            f"CREATE TRIGGER trg_{table}_search_vector "
            f"BEFORE INSERT OR UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION tsvector_{table}()"
        )


def downgrade() -> None:
    for table in PER_TABLE_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_search_vector ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS tsvector_{table}()")

    # Restore original shared function
    op.execute("""
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
""")
    for table in PER_TABLE_TRIGGERS:
        op.execute(
            f"CREATE TRIGGER trg_{table}_search_vector "
            f"BEFORE INSERT OR UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION update_search_vector()"
        )
