"""Add parent_operation_id to trading_operations

Revision ID: 021
Revises: 020
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trading_operations",
        sa.Column("parent_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_trades_parent", "trading_operations", ["parent_operation_id"])


def downgrade() -> None:
    op.drop_index("ix_trades_parent", table_name="trading_operations")
    op.drop_column("trading_operations", "parent_operation_id")
