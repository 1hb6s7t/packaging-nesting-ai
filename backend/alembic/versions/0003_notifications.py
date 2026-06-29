"""add notifications

Revision ID: 0003_notifications
Revises: 0002_work_task_controls
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_notifications"
down_revision = "0002_work_task_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "notification" in inspector.get_table_names():
        return
    op.create_table(
        "notification",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(length=120), nullable=True),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "notification" in inspector.get_table_names():
        op.drop_table("notification")
