"""Create processing_queue table

Revision ID: 012
Revises: 011
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processing_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("raw_info_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), server_default=sa.text("'ingested'")),
        sa.Column("status_history", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("agent_assigned", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_queue_raw_info", "processing_queue", ["raw_info_id"], unique=True)
    op.create_index("ix_queue_status", "processing_queue", ["status"])
    op.create_index("ix_queue_priority", "processing_queue", ["priority"])
    op.create_index("ix_queue_agent", "processing_queue", ["agent_assigned"])


def downgrade() -> None:
    op.drop_table("processing_queue")
