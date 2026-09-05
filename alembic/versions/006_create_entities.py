"""Create entities table

Revision ID: 006
Revises: 005
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String(255)), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("linked_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_entities_norm_name_type", "entities", ["normalized_name", "entity_type"], unique=True)
    op.create_index("ix_entities_type", "entities", ["entity_type"])
    op.create_index("ix_entities_node", "entities", ["linked_node_id"])
    op.create_index("ix_entities_aliases", "entities", ["aliases"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("entities")
