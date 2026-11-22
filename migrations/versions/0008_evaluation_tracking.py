"""Evaluation tracking for the autonomy cycle.

Revision ID: 0008_evaluation_tracking
Revises: 0007_autonomy_classification
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_evaluation_tracking"
down_revision = "0007_autonomy_classification"
branch_labels = None
depends_on = None

EVALUATION_COLUMNS = (
    # The last time the autonomy cycle looked at this opportunity. A NULL
    # value means it was never evaluated.
    ("last_evaluated_at", sa.DateTime(timezone=True), None),
    # How many times the autonomy cycle has tried to evaluate it.
    ("evaluation_attempts", sa.Integer(), "0"),
)


def _column_exists(table: str, name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return name in {column["name"] for column in inspector.get_columns(table)}


def upgrade():
    for name, type_, default in EVALUATION_COLUMNS:
        if _column_exists("opportunities", name):
            continue
        nullable = default is None
        op.add_column(
            "opportunities",
            sa.Column(
                name,
                type_,
                nullable=nullable,
                server_default=sa.text(default) if default is not None else None,
            ),
        )

    inspector = sa.inspect(op.get_bind())
    if "ix_opportunities_last_evaluated_at" not in {
        index["name"] for index in inspector.get_indexes("opportunities")
    }:
        op.create_index(
            "ix_opportunities_last_evaluated_at",
            "opportunities",
            ["last_evaluated_at"],
        )


def downgrade():
    op.drop_index("ix_opportunities_last_evaluated_at", table_name="opportunities")
    for name, _type, _default in EVALUATION_COLUMNS:
        if _column_exists("opportunities", name):
            op.drop_column("opportunities", name)
