"""Add performance indexes

Revision ID: 023
Revises: 022
Create Date: 2026-06-02
"""
from typing import Sequence, Union

from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processing_queue_preprocess_priority_created "
        "ON processing_queue (preprocess_status, priority DESC, created_at ASC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processing_queue_status_priority "
        "ON processing_queue (status, priority DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_world_nodes_active_type_name "
        "ON world_nodes (is_active, node_type, name)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_world_nodes_active_type_name")
    op.execute("DROP INDEX IF EXISTS ix_processing_queue_status_priority")
    op.execute("DROP INDEX IF EXISTS ix_processing_queue_preprocess_priority_created")
