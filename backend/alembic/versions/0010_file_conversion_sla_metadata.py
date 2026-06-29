"""add file conversion SLA metadata

Revision ID: 0010_file_conversion_sla_metadata
Revises: 0009_message_templates
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_file_conversion_sla_metadata"
down_revision = "0009_message_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("file_conversion_job"):
        columns = {column["name"] for column in inspector.get_columns("file_conversion_job")}
        if "metadata_json" not in columns:
            op.add_column("file_conversion_job", sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("file_conversion_job"):
        columns = {column["name"] for column in inspector.get_columns("file_conversion_job")}
        if "metadata_json" in columns:
            op.drop_column("file_conversion_job", "metadata_json")
