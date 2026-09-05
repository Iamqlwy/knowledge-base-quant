"""Create conflict_detections table

Revision ID: 014
Revises: 013
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conflict_detections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("existing_claim", sa.Text(), nullable=False),
        sa.Column("conflicting_claim", sa.Text(), nullable=False),
        sa.Column("existing_evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conflicting_evidence_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conflict_type", sa.String(50), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_conflicts_node", "conflict_detections", ["node_id"])
    op.create_index("ix_conflicts_type", "conflict_detections", ["conflict_type"])
    op.create_index("ix_conflicts_unresolved", "conflict_detections", ["resolved_at"])


def downgrade() -> None:
    op.drop_table("conflict_detections")
