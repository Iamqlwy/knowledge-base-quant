"""Create trading_operations table

Revision ID: 004
Revises: 003
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trading_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_type", sa.String(50), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger_raw_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("quantity", sa.Numeric(), nullable=True),
        sa.Column("price", sa.Numeric(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("expected_impact", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(50), nullable=True),
        sa.Column("status", sa.String(50), server_default=sa.text("'pending'")),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_trades_type", "trading_operations", ["operation_type"])
    op.create_index("ix_trades_node", "trading_operations", ["target_node_id"])
    op.create_index("ix_trades_analysis", "trading_operations", ["trigger_analysis_id"])
    op.create_index("ix_trades_status", "trading_operations", ["status"])


def downgrade() -> None:
    op.drop_table("trading_operations")
