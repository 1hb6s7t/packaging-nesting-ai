"""add task heartbeat metrics

Revision ID: 0005_task_heartbeat_metrics
Revises: 0004_export_lifecycle
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_task_heartbeat_metrics"
down_revision = "0004_export_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("work_task")}
    if "progress_percent" not in columns:
        op.add_column("work_task", sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"))
    if "heartbeat_at" not in columns:
        op.add_column("work_task", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("work_task")}
    for column_name in ["heartbeat_at", "progress_percent"]:
        if column_name in columns:
            op.drop_column("work_task", column_name)
