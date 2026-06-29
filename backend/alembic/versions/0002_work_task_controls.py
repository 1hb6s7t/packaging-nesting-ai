"""add work task controls

Revision ID: 0002_work_task_controls
Revises: 0001_initial_schema
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_work_task_controls"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("work_task")}
    if "parent_task_id" not in columns:
        op.add_column("work_task", sa.Column("parent_task_id", sa.String(length=64), nullable=True))
    if "attempt" not in columns:
        op.add_column("work_task", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    if "max_attempts" not in columns:
        op.add_column("work_task", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
    if "timeout_sec" not in columns:
        op.add_column("work_task", sa.Column("timeout_sec", sa.Integer(), nullable=True))
    if "cancel_requested" not in columns:
        op.add_column("work_task", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("work_task")}
    for column_name in ["cancel_requested", "timeout_sec", "max_attempts", "attempt", "parent_task_id"]:
        if column_name in columns:
            op.drop_column("work_task", column_name)
