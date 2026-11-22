"""The publication date of a collected post.

Revision ID: 0009_post_published_at
Revises: 0008_evaluation_tracking
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_post_published_at"
down_revision = "0008_evaluation_tracking"
branch_labels = None
depends_on = None


def _column_exists(table: str, name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return name in {column["name"] for column in inspector.get_columns(table)}


def upgrade():
    # The date the post was published in the channel. Without it a post that
    # was published years ago looks exactly like one from an hour ago, and
    # the review queue fills with posts nobody can act on.
    if not _column_exists("collected_posts", "published_at"):
        op.add_column(
            "collected_posts",
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    if _column_exists("collected_posts", "published_at"):
        op.drop_column("collected_posts", "published_at")
