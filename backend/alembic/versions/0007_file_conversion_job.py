"""add file conversion job table

Revision ID: 0007_file_conversion_job
Revises: 0006_adapter_config_versioning
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_file_conversion_job"
down_revision = "0006_adapter_config_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("file_conversion_job"):
        return
    op.create_table(
        "file_conversion_job",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("artwork_file_id", sa.String(length=64), nullable=False),
        sa.Column("source_format", sa.String(length=32), nullable=False),
        sa.Column("target_format", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["artwork_file_id"], ["artwork_file.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("file_conversion_job"):
        op.drop_table("file_conversion_job")
