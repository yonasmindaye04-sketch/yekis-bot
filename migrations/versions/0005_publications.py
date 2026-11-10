"""Publication records for approval-gated Telegram publishing.

Revision ID: 0005_publications
Revises: 0004_review_audit
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_publications"
down_revision = "0004_review_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunities.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "opportunity_id", "channel", name="uq_publication_opportunity_channel"
        ),
    )
    op.create_index("ix_publications_opportunity_id", "publications", ["opportunity_id"])
    op.create_index("ix_publications_status", "publications", ["status"])


def downgrade():
    op.drop_index("ix_publications_status", table_name="publications")
    op.drop_index("ix_publications_opportunity_id", table_name="publications")
    op.drop_table("publications")
