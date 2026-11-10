"""Telegram collector checkpoints, source health, and risk flags.

Revision ID: 0002_telegram_collector
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_telegram_collector"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sources", sa.Column("last_collected_message_id", sa.Integer(), nullable=True))
    op.add_column(
        "sources", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sources", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sources", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sources",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column("sources", sa.Column("last_error", sa.String(length=500), nullable=True))
    op.create_index("ix_sources_source_type", "sources", ["source_type"])

    op.add_column(
        "opportunities",
        sa.Column("risk_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "opportunities",
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade():
    op.drop_column("opportunities", "risk_score")
    op.drop_column("opportunities", "risk_flags")
    op.drop_index("ix_sources_source_type", table_name="sources")
    op.drop_column("sources", "last_error")
    op.drop_column("sources", "consecutive_failures")
    op.drop_column("sources", "next_attempt_at")
    op.drop_column("sources", "last_success_at")
    op.drop_column("sources", "last_attempt_at")
    op.drop_column("sources", "last_collected_message_id")
