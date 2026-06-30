"""add enterprise benchmark metrics

Revision ID: 0014_benchmark_enterprise_metrics
Revises: 0013_operation_log_indexes
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_benchmark_enterprise_metrics"
down_revision = "0013_operation_log_indexes"
branch_labels = None
depends_on = None


COLUMNS = {
    "planning_mode": sa.Column("planning_mode", sa.String(length=40), nullable=False, server_default="single_sheet"),
    "hard_rule_pass": sa.Column("hard_rule_pass", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    "quantity_fulfillment_rate": sa.Column(
        "quantity_fulfillment_rate", sa.Float(), nullable=False, server_default="0"
    ),
    "requested_units": sa.Column("requested_units", sa.Integer(), nullable=False, server_default="0"),
    "produced_units": sa.Column("produced_units", sa.Integer(), nullable=False, server_default="0"),
    "shortage_units": sa.Column("shortage_units", sa.Integer(), nullable=False, server_default="0"),
    "overproduction_units": sa.Column("overproduction_units", sa.Integer(), nullable=False, server_default="0"),
    "units_per_sheet": sa.Column("units_per_sheet", sa.Integer(), nullable=False, server_default="0"),
    "sheets_used": sa.Column("sheets_used", sa.Integer(), nullable=False, server_default="0"),
    "peak_rss_mb": sa.Column("peak_rss_mb", sa.Float(), nullable=True),
    "export_ok": sa.Column("export_ok", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    "case_score": sa.Column("case_score", sa.Float(), nullable=False, server_default="0"),
    "baseline_delta_utilization_rate": sa.Column("baseline_delta_utilization_rate", sa.Float(), nullable=True),
    "p95_runtime_ms": sa.Column("p95_runtime_ms", sa.Integer(), nullable=True),
    "metrics_json": sa.Column("metrics_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("benchmark_run"):
        return
    existing_columns = {column["name"] for column in inspector.get_columns("benchmark_run")}
    for name, column in COLUMNS.items():
        if name not in existing_columns:
            op.add_column("benchmark_run", column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("benchmark_run"):
        return
    existing_columns = {column["name"] for column in inspector.get_columns("benchmark_run")}
    for name in reversed(COLUMNS):
        if name in existing_columns:
            op.drop_column("benchmark_run", name)
