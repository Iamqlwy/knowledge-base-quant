"""Drop parent_node_id and create world_node_edges M:N table.

Revision ID: 029
Revises: 1819b1b1eb25
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "029"
down_revision: Union[str, None] = "1819b1b1eb25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop the parent_node_id column and its index
    op.drop_index("ix_world_nodes_parent", table_name="world_nodes")
    op.drop_column("world_nodes", "parent_node_id")

    # 2. Create world_node_edges table
    op.create_table(
        "world_node_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("parent_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=False, server_default="belongs_to"),
        sa.Column("weight", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("evidence_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
    )
    op.create_index("ix_wne_parent", "world_node_edges", ["parent_node_id"])
    op.create_index("ix_wne_child", "world_node_edges", ["child_node_id"])
    op.create_index("ix_wne_unique_edge", "world_node_edges", ["parent_node_id", "child_node_id", "relationship_type"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_wne_unique_edge", table_name="world_node_edges")
    op.drop_index("ix_wne_child", table_name="world_node_edges")
    op.drop_index("ix_wne_parent", table_name="world_node_edges")
    op.drop_table("world_node_edges")

    op.add_column("world_nodes", sa.Column("parent_node_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_world_nodes_parent", "world_nodes", ["parent_node_id"])
