"""Create analyses table

Revision ID: 003
Revises: 002
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("analysis_type", sa.String(50), nullable=False),
        sa.Column("agent_id", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("parent_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("root_raw_info_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("time_horizon", sa.String(50), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_analyses_type", "analyses", ["analysis_type"])
    op.create_index("ix_analyses_agent", "analyses", ["agent_id"])
    op.create_index("ix_analyses_confidence", "analyses", ["confidence"])
    op.create_index("ix_analyses_search", "analyses", ["search_vector"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("analyses")
