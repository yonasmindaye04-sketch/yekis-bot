"""Classification results, autonomy settings, and the autonomy decision log.

Revision ID: 0007_autonomy_classification
Revises: 0006_opportunity_client_name
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_autonomy_classification"
down_revision = "0006_opportunity_client_name"
branch_labels = None
depends_on = None

# (name, type, server default literal). A column without a default keeps the
# nullability of the matching model attribute.
OPPORTUNITY_COLUMNS = (
    ("classification", sa.String(length=40), "'insufficient_information'"),
    ("engagement_type", sa.String(length=40), None),
    ("is_genuine_opportunity", sa.Boolean(), "false"),
    ("is_publishable", sa.Boolean(), "false"),
    ("classification_confidence", sa.Float(), "0.0"),
    ("classification_evidence", sa.JSON(), "'[]'"),
    ("classification_reasons", sa.JSON(), "'[]'"),
    ("classification_method", sa.String(length=30), "'deterministic'"),
    ("classifier_version", sa.String(length=30), None),
    ("model_provider", sa.String(length=30), None),
    ("model_name", sa.String(length=120), None),
    ("risk_level", sa.String(length=20), "'none'"),
    ("risk_assessment", sa.JSON(), "'{}'"),
    ("approval_method", sa.String(length=20), None),
    ("approval_reason", sa.Text(), None),
    ("approved_at", sa.DateTime(timezone=True), None),
    ("approved_by", sa.String(length=120), None),
    ("quarantined", sa.Boolean(), "false"),
    ("deadline", sa.String(length=120), None),
    ("application_url", sa.String(length=1000), None),
)

PUBLICATION_COLUMNS = (
    ("is_autonomous", sa.Boolean(), "false"),
    ("autonomy_mode", sa.String(length=20), None),
    ("content_hash", sa.String(length=64), None),
)


def _add_column(table: str, name: str, type_, default: str | None) -> None:
    """Add a column only when it does not exist yet (idempotent upgrade).

    A column with a server default is added as NOT NULL: the default fills
    the existing rows, so no separate backfill statement is needed.
    """
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table)}
    if name in existing:
        return
    if default is None:
        op.add_column(table, sa.Column(name, type_, nullable=True))
        return
    op.add_column(
        table,
        sa.Column(
            name,
            type_,
            nullable=False,
            server_default=sa.text(default),
        ),
    )


def upgrade():
    for name, type_, default in OPPORTUNITY_COLUMNS:
        _add_column("opportunities", name, type_, default)

    for name, type_, default in PUBLICATION_COLUMNS:
        _add_column("publications", name, type_, default)

    inspector = sa.inspect(op.get_bind())
    opportunity_indexes = {
        index["name"] for index in inspector.get_indexes("opportunities")
    }
    if "ix_opportunities_classification" not in opportunity_indexes:
        op.create_index(
            "ix_opportunities_classification", "opportunities", ["classification"]
        )
    if "ix_opportunities_risk_level" not in opportunity_indexes:
        op.create_index("ix_opportunities_risk_level", "opportunities", ["risk_level"])

    publication_indexes = {
        index["name"] for index in inspector.get_indexes("publications")
    }
    if "ix_publications_content_hash" not in publication_indexes:
        op.create_index(
            "ix_publications_content_hash", "publications", ["content_hash"]
        )

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "autonomy_settings" not in tables:
        op.create_table(
            "autonomy_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("mode", sa.String(length=20), nullable=False),
            sa.Column("auto_approval_threshold", sa.Float(), nullable=False),
            sa.Column(
                "approved_source_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column(
                "approved_categories",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
            sa.Column("daily_publish_limit", sa.Integer(), nullable=False),
            sa.Column("min_interval_seconds", sa.Integer(), nullable=False),
            sa.Column("pilot_channel", sa.String(length=255), nullable=True),
            sa.Column("target_channel", sa.String(length=255), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by", sa.String(length=120), nullable=True),
        )

    if "autonomy_decisions" not in tables:
        op.create_table(
            "autonomy_decisions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "opportunity_id",
                sa.Integer(),
                sa.ForeignKey("opportunities.id"),
                nullable=False,
            ),
            sa.Column("source_id", sa.Integer(), nullable=True),
            sa.Column("decision", sa.String(length=30), nullable=False),
            sa.Column("classification", sa.String(length=40), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("threshold", sa.Float(), nullable=False),
            sa.Column("mode", sa.String(length=20), nullable=False),
            sa.Column("actor", sa.String(length=120), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_autonomy_decisions_opportunity_id",
            "autonomy_decisions",
            ["opportunity_id"],
        )
        op.create_index(
            "ix_autonomy_decisions_decision", "autonomy_decisions", ["decision"]
        )
        op.create_index(
            "ix_autonomy_decisions_classification",
            "autonomy_decisions",
            ["classification"],
        )
        op.create_index(
            "ix_autonomy_decisions_source_id", "autonomy_decisions", ["source_id"]
        )
        op.create_index(
            "ix_autonomy_decisions_created_at", "autonomy_decisions", ["created_at"]
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "autonomy_decisions" in tables:
        for index in (
            "ix_autonomy_decisions_created_at",
            "ix_autonomy_decisions_source_id",
            "ix_autonomy_decisions_classification",
            "ix_autonomy_decisions_decision",
            "ix_autonomy_decisions_opportunity_id",
        ):
            op.drop_index(index, table_name="autonomy_decisions")
        op.drop_table("autonomy_decisions")

    if "autonomy_settings" in tables:
        op.drop_table("autonomy_settings")

    publication_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("publications")
    }
    if "ix_publications_content_hash" in publication_indexes:
        op.drop_index("ix_publications_content_hash", table_name="publications")

    opportunity_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("opportunities")
    }
    if "ix_opportunities_risk_level" in opportunity_indexes:
        op.drop_index("ix_opportunities_risk_level", table_name="opportunities")
    if "ix_opportunities_classification" in opportunity_indexes:
        op.drop_index("ix_opportunities_classification", table_name="opportunities")

    for name, _type, _default in reversed(PUBLICATION_COLUMNS):
        op.drop_column("publications", name)
    for name, _type, _default in reversed(OPPORTUNITY_COLUMNS):
        op.drop_column("opportunities", name)
