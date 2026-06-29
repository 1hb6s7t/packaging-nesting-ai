"""add production operations archive tables

Revision ID: 0008_production_operations_tables
Revises: 0007_file_conversion_job
Create Date: 2026-06-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_production_operations_tables"
down_revision = "0007_file_conversion_job"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("production_schedule_entry"):
        op.create_table(
            "production_schedule_entry",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("external_system_id", sa.String(length=64), nullable=False),
            sa.Column("sync_task_id", sa.String(length=64), nullable=True),
            sa.Column("external_id", sa.String(length=120), nullable=False),
            sa.Column("order_id", sa.String(length=120), nullable=True),
            sa.Column("job_id", sa.String(length=120), nullable=True),
            sa.Column("line_code", sa.String(length=120), nullable=True),
            sa.Column("machine_code", sa.String(length=120), nullable=True),
            sa.Column("workstation", sa.String(length=120), nullable=True),
            sa.Column("planned_start_at", sa.String(length=80), nullable=True),
            sa.Column("planned_end_at", sa.String(length=80), nullable=True),
            sa.Column("status", sa.String(length=80), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("fields", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["external_system_id"], ["external_system.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if not inspector.has_table("inventory_snapshot"):
        op.create_table(
            "inventory_snapshot",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("external_system_id", sa.String(length=64), nullable=False),
            sa.Column("sync_task_id", sa.String(length=64), nullable=True),
            sa.Column("external_id", sa.String(length=120), nullable=False),
            sa.Column("material_code", sa.String(length=120), nullable=True),
            sa.Column("material_name", sa.String(length=255), nullable=True),
            sa.Column("batch_no", sa.String(length=120), nullable=True),
            sa.Column("warehouse_code", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=80), nullable=True),
            sa.Column("available_qty", sa.Float(), nullable=True),
            sa.Column("reserved_qty", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=40), nullable=True),
            sa.Column("fields", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["external_system_id"], ["external_system.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if not inspector.has_table("delivery_confirmation"):
        op.create_table(
            "delivery_confirmation",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("external_system_id", sa.String(length=64), nullable=False),
            sa.Column("sync_task_id", sa.String(length=64), nullable=True),
            sa.Column("external_id", sa.String(length=120), nullable=False),
            sa.Column("order_id", sa.String(length=120), nullable=True),
            sa.Column("shipment_no", sa.String(length=120), nullable=True),
            sa.Column("carrier", sa.String(length=120), nullable=True),
            sa.Column("tracking_no", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=80), nullable=True),
            sa.Column("shipped_at", sa.String(length=80), nullable=True),
            sa.Column("delivered_at", sa.String(length=80), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("fields", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["external_system_id"], ["external_system.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in ("delivery_confirmation", "inventory_snapshot", "production_schedule_entry"):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
