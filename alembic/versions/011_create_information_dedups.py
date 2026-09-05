"""Create information_dedups table

Revision ID: 011
Revises: 010
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "information_dedups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("primary_info_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("duplicate_info_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedup_type", sa.String(50), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("dedup_rationale", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_dedup_unique", "information_dedups", ["primary_info_id", "duplicate_info_id"], unique=True)
    op.create_index("ix_dedup_duplicate", "information_dedups", ["duplicate_info_id"])


def downgrade() -> None:
    op.drop_table("information_dedups")
