"""Durable job executions and dead-letter records for the M2 worker.

Revision ID: 0003_job_pipeline
Revises: 0002_telegram_collector
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_job_pipeline"
down_revision = "0002_telegram_collector"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_name", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_executions_status", "job_executions", ["status"])

    op.create_table(
        "dead_letter_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_execution_id",
            sa.Integer(),
            sa.ForeignKey("job_executions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("task_name", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_dead_letter_jobs_idempotency_key", "dead_letter_jobs", ["idempotency_key"]
    )


def downgrade():
    op.drop_index("ix_dead_letter_jobs_idempotency_key", table_name="dead_letter_jobs")
    op.drop_table("dead_letter_jobs")
    op.drop_index("ix_job_executions_status", table_name="job_executions")
    op.drop_table("job_executions")
