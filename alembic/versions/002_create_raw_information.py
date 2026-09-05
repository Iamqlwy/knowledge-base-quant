"""Create raw_information table

Revision ID: 002
Revises: 001
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "raw_information",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("info_type", sa.String(50), nullable=False),
        sa.Column("language", sa.String(10), server_default=sa.text("'zh'")),
        sa.Column("raw_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("processing_status", sa.String(50), server_default=sa.text("'ingested'")),
        sa.Column("importance_score", sa.Float(), server_default=sa.text("0.0")),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_raw_info_content_hash", "raw_information", ["content_hash"], unique=True)
    op.create_index("ix_raw_info_source", "raw_information", ["source"])
    op.create_index("ix_raw_info_published", "raw_information", ["published_at"])
    op.create_index("ix_raw_info_type", "raw_information", ["info_type"])
    op.create_index("ix_raw_info_status", "raw_information", ["processing_status"])
    op.create_index("ix_raw_info_search", "raw_information", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_raw_info_importance", "raw_information", ["importance_score"])


def downgrade() -> None:
    op.drop_table("raw_information")
