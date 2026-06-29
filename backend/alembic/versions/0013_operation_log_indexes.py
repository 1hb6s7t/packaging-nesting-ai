"""add operation log query indexes

Revision ID: 0013_operation_log_indexes
Revises: 0012_notification_recipient_groups
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_operation_log_indexes"
down_revision = "0012_notification_recipient_groups"
branch_labels = None
depends_on = None


INDEXES = {
    "ix_operation_log_created_at": ["created_at"],
    "ix_operation_log_action_created_at": ["action", "created_at"],
    "ix_operation_log_target_created_at": ["target_type", "target_id", "created_at"],
    "ix_operation_log_actor_created_at": ["actor_id", "created_at"],
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("operation_log"):
        return
    existing_indexes = {item["name"] for item in inspector.get_indexes("operation_log")}
    for name, columns in INDEXES.items():
        if name not in existing_indexes:
            op.create_index(name, "operation_log", columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("operation_log"):
        return
    existing_indexes = {item["name"] for item in inspector.get_indexes("operation_log")}
    for name in reversed(INDEXES):
        if name in existing_indexes:
            op.drop_index(name, table_name="operation_log")
