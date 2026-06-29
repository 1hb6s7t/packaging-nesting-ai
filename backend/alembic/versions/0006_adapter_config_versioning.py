"""add adapter config versioning

Revision ID: 0006_adapter_config_versioning
Revises: 0005_task_heartbeat_metrics
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_adapter_config_versioning"
down_revision = "0005_task_heartbeat_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("adapter_config")}
    if "version" not in columns:
        op.add_column("adapter_config", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    if "is_active" not in columns:
        op.add_column("adapter_config", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "validation_status" not in columns:
        op.add_column(
            "adapter_config",
            sa.Column("validation_status", sa.String(length=40), nullable=False, server_default="untested"),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("adapter_config")}
    for column_name in ["validation_status", "is_active", "version"]:
        if column_name in columns:
            op.drop_column("adapter_config", column_name)
