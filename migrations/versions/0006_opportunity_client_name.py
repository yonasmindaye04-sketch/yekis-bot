"""Add client_name to opportunities table.

Revision ID: 0006_opportunity_client_name
Revises: 0005_publications
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_opportunity_client_name"
down_revision = "0005_publications"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("opportunities")}
    if "client_name" in existing:
        return
    op.add_column(
        "opportunities",
        sa.Column("client_name", sa.String(length=250), nullable=True),
    )


def downgrade():
    op.drop_column("opportunities", "client_name")
