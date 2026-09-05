"""Add preprocess_status to processing_queue

Revision ID: 020
Revises: 019
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "processing_queue",
        sa.Column(
            "preprocess_status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'ingested'"),
        ),
    )
    op.create_index(
        "ix_queue_preprocess_status",
        "processing_queue",
        ["preprocess_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_queue_preprocess_status", table_name="processing_queue")
    op.drop_column("processing_queue", "preprocess_status")
