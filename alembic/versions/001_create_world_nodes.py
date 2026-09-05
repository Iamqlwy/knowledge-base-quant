"""Create world_nodes table

Revision ID: 001
Revises:
Create Date: 2026-05-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "world_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("node_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_world_nodes_name_type", "world_nodes", ["name", "node_type"], unique=True)
    op.create_index("ix_world_nodes_type", "world_nodes", ["node_type"])
    op.create_index("ix_world_nodes_parent", "world_nodes", ["parent_node_id"])
    op.create_index("ix_world_nodes_search", "world_nodes", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_world_nodes_aliases", "world_nodes", ["aliases"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("world_nodes")
