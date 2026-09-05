"""Create feedbacks table

Revision ID: 005
Revises: 004
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedbacks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("trigger_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger_trade_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_outcome", sa.Text(), nullable=True),
        sa.Column("actual_outcome", sa.Text(), nullable=True),
        sa.Column("judgment_correct", sa.Boolean(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("missed_factors", sa.Text(), nullable=True),
        sa.Column("adjustment_suggestions", sa.Text(), nullable=True),
        sa.Column("market_environment_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("lessons_learned", sa.Text(), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_feedbacks_analysis", "feedbacks", ["trigger_analysis_id"])
    op.create_index("ix_feedbacks_trade", "feedbacks", ["trigger_trade_id"])
    op.create_index("ix_feedbacks_correct", "feedbacks", ["judgment_correct"])
    op.create_index("ix_feedbacks_search", "feedbacks", ["search_vector"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("feedbacks")
