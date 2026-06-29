"""add notification recipient groups and user organization fields

Revision ID: 0012_notification_recipient_groups
Revises: 0011_solution_export_storage_object_metadata
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_notification_recipient_groups"
down_revision = "0011_solution_export_storage_object_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    if "user_account" in table_names:
        user_columns = {column["name"] for column in inspector.get_columns("user_account")}
        user_additions = {
            "org_unit_code": sa.Column("org_unit_code", sa.String(length=120), nullable=True),
            "org_unit_name": sa.Column("org_unit_name", sa.String(length=120), nullable=True),
            "job_title": sa.Column("job_title", sa.String(length=120), nullable=True),
            "external_user_id": sa.Column("external_user_id", sa.String(length=120), nullable=True),
        }
        for column_name, column in user_additions.items():
            if column_name not in user_columns:
                op.add_column("user_account", column)

    if "message_template" in table_names:
        template_columns = {column["name"] for column in inspector.get_columns("message_template")}
        template_additions = {
            "recipient_group_id": sa.Column("recipient_group_id", sa.String(length=64), nullable=True),
            "escalation_group_id": sa.Column("escalation_group_id", sa.String(length=64), nullable=True),
        }
        for column_name, column in template_additions.items():
            if column_name not in template_columns:
                op.add_column("message_template", column)

    if "notification_recipient_group" not in table_names:
        op.create_table(
            "notification_recipient_group",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("member_user_ids", sa.JSON(), nullable=False),
            sa.Column("permission_codes", sa.JSON(), nullable=False),
            sa.Column("department_codes", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    if "notification_recipient_group" in table_names:
        op.drop_table("notification_recipient_group")
    if "message_template" in table_names:
        template_columns = {column["name"] for column in inspector.get_columns("message_template")}
        for column_name in ["escalation_group_id", "recipient_group_id"]:
            if column_name in template_columns:
                op.drop_column("message_template", column_name)
    if "user_account" in table_names:
        user_columns = {column["name"] for column in inspector.get_columns("user_account")}
        for column_name in ["external_user_id", "job_title", "org_unit_name", "org_unit_code"]:
            if column_name in user_columns:
                op.drop_column("user_account", column_name)
