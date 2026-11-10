"""Review notes, reviewer identity, status index, and append-only audit events.

Revision ID: 0004_review_audit
Revises: 0003_job_pipeline
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_review_audit"
down_revision = "0003_job_pipeline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("opportunities", sa.Column("reviewed_by", sa.String(length=120), nullable=True))
    op.add_column("opportunities", sa.Column("reviewer_note", sa.Text(), nullable=True))
    op.create_index(
        "ix_opportunities_review_status", "opportunities", ["review_status"]
    )

    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunities.id"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_events_opportunity_id", "review_events", ["opportunity_id"])


def downgrade():
    op.drop_index("ix_review_events_opportunity_id", table_name="review_events")
    op.drop_table("review_events")
    op.drop_index("ix_opportunities_review_status", table_name="opportunities")
    op.drop_column("opportunities", "reviewer_note")
    op.drop_column("opportunities", "reviewed_by")
