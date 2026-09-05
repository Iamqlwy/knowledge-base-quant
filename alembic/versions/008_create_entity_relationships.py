"""Create entity_relationships table

Revision ID: 008
Revises: 007
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=False),
        sa.Column("strength", sa.Float(), nullable=True),
        sa.Column("evidence_info_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.String(50), nullable=True),
        sa.Column("valid_until", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_er_source_type", "entity_relationships", ["source_entity_id", "relationship_type"])
    op.create_index("ix_er_target_type", "entity_relationships", ["target_entity_id", "relationship_type"])


def downgrade() -> None:
    op.drop_table("entity_relationships")
