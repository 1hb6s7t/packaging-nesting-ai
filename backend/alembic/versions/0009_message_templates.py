"""add message templates and dispatch logs

Revision ID: 0009_message_templates
Revises: 0008_production_operations_tables
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_message_templates"
down_revision = "0008_production_operations_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("message_template"):
        op.create_table(
            "message_template",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("channel", sa.String(length=40), nullable=False),
            sa.Column("title_template", sa.String(length=240), nullable=False),
            sa.Column("message_template", sa.Text(), nullable=False),
            sa.Column("recipient_permission_code", sa.String(length=120), nullable=True),
            sa.Column("escalation_permission_code", sa.String(length=120), nullable=True),
            sa.Column("escalation_after_minutes", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if not inspector.has_table("message_dispatch_log"):
        op.create_table(
            "message_dispatch_log",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("template_id", sa.String(length=64), nullable=True),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("channel", sa.String(length=40), nullable=False),
            sa.Column("target_type", sa.String(length=120), nullable=True),
            sa.Column("target_id", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("recipient_count", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in ("message_dispatch_log", "message_template"):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
