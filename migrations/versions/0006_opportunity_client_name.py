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
    op.add_column(
        "opportunities",
        sa.Column("client_name", sa.String(length=250), nullable=True),
    )


def downgrade():
    op.drop_column("opportunities", "client_name")
