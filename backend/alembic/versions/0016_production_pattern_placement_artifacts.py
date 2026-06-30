"""add production pattern placement artifacts

Revision ID: 0016_production_pattern_placement_artifacts
Revises: 0015_batch_layout_enterprise_tables
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_production_pattern_placement_artifacts"
down_revision = "0015_batch_layout_enterprise_tables"
branch_labels = None
depends_on = None


COLUMNS = {
    "placement_json": sa.Column("placement_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    "placement_svg": sa.Column("placement_svg", sa.Text(), nullable=False, server_default=""),
    "placement_checksum": sa.Column("placement_checksum", sa.String(length=128), nullable=True),
    "placement_solver_json": sa.Column(
        "placement_solver_json",
        sa.JSON(),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("production_pattern"):
        return
    existing_columns = {column["name"] for column in inspector.get_columns("production_pattern")}
    for name, column in COLUMNS.items():
        if name not in existing_columns:
            op.add_column("production_pattern", column)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("production_pattern"):
        return
    existing_columns = {column["name"] for column in inspector.get_columns("production_pattern")}
    for name in reversed(COLUMNS):
        if name in existing_columns:
            op.drop_column("production_pattern", name)
