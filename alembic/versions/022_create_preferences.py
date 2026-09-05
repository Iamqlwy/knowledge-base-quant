"""Create structured_preferences and industry_cognitions tables

Revision ID: 022
Revises: 021
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "structured_preferences",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "asset_preferences",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text(
                "'{\"sector_weights\": {}, \"avoid_list\": [], \"market_cap_preference\": \"any\", \"whitelist\": []}'"
            ),
        ),
        sa.Column(
            "risk_preferences",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text(
                "'{\"position_limits\": {}, \"max_drawdown_pct\": 20.0, \"stop_loss_pct\": 10.0, \"take_profit_pct\": 30.0}'"
            ),
        ),
        sa.Column(
            "analysis_preferences",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text(
                "'{\"time_horizon\": \"medium\", \"depth\": \"standard\", \"focus_points\": []}'"
            ),
        ),
        sa.Column(
            "learned_rules",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "industry_cognitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("sector", sa.Text(), nullable=False),
        sa.Column("cognition_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("append_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_industry_cognitions_sector", "industry_cognitions", ["sector"], unique=True)


def downgrade() -> None:
    op.drop_table("industry_cognitions")
    op.drop_table("structured_preferences")
