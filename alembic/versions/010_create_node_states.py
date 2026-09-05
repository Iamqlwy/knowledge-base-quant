"""Create node_states table

Revision ID: 010
Revises: 009
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "node_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("core_logic", sa.Text(), nullable=True),
        sa.Column("primary_drivers", postgresql.JSONB(), nullable=True),
        sa.Column("risks", postgresql.JSONB(), nullable=True),
        sa.Column("focus_points", postgresql.JSONB(), nullable=True),
        sa.Column("recent_changes", sa.Text(), nullable=True),
        sa.Column("uncertainty_flags", postgresql.JSONB(), nullable=True),
        sa.Column("key_evidence_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("state_summary", sa.Text(), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_node_states_version", "node_states", ["node_id", "version"], unique=True)
    op.create_index("ix_node_states_node", "node_states", ["node_id"])
    op.create_index("ix_node_states_current", "node_states", ["node_id", "effective_to"])
    op.create_index("ix_node_states_search", "node_states", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_node_states_evidence", "node_states", ["key_evidence_ids"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("node_states")
