"""add solution export storage object metadata

Revision ID: 0011_solution_export_storage_object_metadata
Revises: 0010_file_conversion_sla_metadata
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_solution_export_storage_object_metadata"
down_revision = "0010_file_conversion_sla_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("solution_export"):
        return
    columns = {column["name"] for column in inspector.get_columns("solution_export")}
    additions = {
        "storage_backend": sa.Column("storage_backend", sa.String(length=40), nullable=True),
        "storage_object_key": sa.Column("storage_object_key", sa.String(length=500), nullable=True),
        "storage_version_id": sa.Column("storage_version_id", sa.String(length=255), nullable=True),
        "storage_etag": sa.Column("storage_etag", sa.String(length=255), nullable=True),
        "storage_size_bytes": sa.Column("storage_size_bytes", sa.Integer(), nullable=True),
    }
    for column_name, column in additions.items():
        if column_name not in columns:
            op.add_column("solution_export", column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("solution_export"):
        return
    columns = {column["name"] for column in inspector.get_columns("solution_export")}
    for column_name in [
        "storage_size_bytes",
        "storage_etag",
        "storage_version_id",
        "storage_object_key",
        "storage_backend",
    ]:
        if column_name in columns:
            op.drop_column("solution_export", column_name)
