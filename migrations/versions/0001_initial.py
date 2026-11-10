"""Initial Yekis Intelligence schema."""
import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("category", sa.String(80)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "collected_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("original_url", sa.String(1500)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_source_external_post"),
    )
    op.create_index("ix_collected_posts_content_hash", "collected_posts", ["content_hash"])
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "collected_post_id",
            sa.Integer(),
            sa.ForeignKey("collected_posts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("opportunity_type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("budget_amount", sa.Float()),
        sa.Column("budget_currency", sa.String(12)),
        sa.Column("duration", sa.String(120)),
        sa.Column("remote", sa.Boolean()),
        sa.Column("description", sa.Text()),
        sa.Column("quality_score", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("extraction_method", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("opportunities")
    op.drop_index("ix_collected_posts_content_hash", table_name="collected_posts")
    op.drop_table("collected_posts")
    op.drop_table("sources")
