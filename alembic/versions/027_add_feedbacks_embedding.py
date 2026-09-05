"""Add embedding column to feedbacks table

Revision ID: 027
Revises: 026
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("feedbacks", sa.Column("embedding", Vector(1536), nullable=True))
    op.execute(
        "CREATE INDEX idx_feedbacks_embedding_not_null "
        "ON feedbacks (id) WHERE embedding IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_feedbacks_embedding_not_null")
    op.drop_column("feedbacks", "embedding")
