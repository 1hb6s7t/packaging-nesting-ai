"""add export lifecycle metadata

Revision ID: 0004_export_lifecycle
Revises: 0003_notifications
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_export_lifecycle"
down_revision = "0003_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("solution_export")}
    if "version" not in columns:
        op.add_column("solution_export", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    if "lifecycle_status" not in columns:
        op.add_column(
            "solution_export",
            sa.Column("lifecycle_status", sa.String(length=40), nullable=False, server_default="active"),
        )
    if "retention_until" not in columns:
        op.add_column("solution_export", sa.Column("retention_until", sa.DateTime(), nullable=True))
    if "superseded_by_export_id" not in columns:
        op.add_column("solution_export", sa.Column("superseded_by_export_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("solution_export")}
    for column_name in ["superseded_by_export_id", "retention_until", "lifecycle_status", "version"]:
        if column_name in columns:
            op.drop_column("solution_export", column_name)
