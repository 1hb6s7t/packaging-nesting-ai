"""add batch artwork and production planning tables

Revision ID: 0015_batch_layout_enterprise_tables
Revises: 0014_benchmark_enterprise_metrics
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_batch_layout_enterprise_tables"
down_revision = "0014_benchmark_enterprise_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("batch_upload"):
        op.create_table(
            "batch_upload",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("source_name", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("item_count", sa.Integer(), nullable=False),
            sa.Column("uploaded_count", sa.Integer(), nullable=False),
            sa.Column("preflighted_count", sa.Integer(), nullable=False),
            sa.Column("parsed_count", sa.Integer(), nullable=False),
            sa.Column("conversion_required_count", sa.Integer(), nullable=False),
            sa.Column("manual_review_count", sa.Integer(), nullable=False),
            sa.Column("failed_count", sa.Integer(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_batch_upload_status_created_at", "batch_upload", ["status", "created_at"])

    if not inspector.has_table("batch_artwork_item"):
        op.create_table(
            "batch_artwork_item",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("batch_id", sa.String(length=64), sa.ForeignKey("batch_upload.id"), nullable=False),
            sa.Column("artwork_file_id", sa.String(length=64), sa.ForeignKey("artwork_file.id"), nullable=True),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=120), nullable=True),
            sa.Column("checksum", sa.String(length=128), nullable=True),
            sa.Column("source_format", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("order_id", sa.String(length=120), nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("material", sa.String(length=120), nullable=True),
            sa.Column("thickness", sa.String(length=80), nullable=True),
            sa.Column("print_method", sa.String(length=80), nullable=True),
            sa.Column("spot_color", sa.String(length=120), nullable=True),
            sa.Column("due_date", sa.String(length=40), nullable=True),
            sa.Column("category", sa.String(length=120), nullable=True),
            sa.Column("customer_id", sa.String(length=120), nullable=True),
            sa.Column("preflight_report_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("feature_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("classification", sa.String(length=40), nullable=True),
            sa.Column("parse_error", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_batch_artwork_item_batch_status", "batch_artwork_item", ["batch_id", "status"])
        op.create_index(
            "ix_batch_artwork_item_batch_classification",
            "batch_artwork_item",
            ["batch_id", "classification"],
        )
        op.create_index(
            "ix_batch_artwork_item_compatibility",
            "batch_artwork_item",
            ["material", "thickness", "print_method", "spot_color"],
        )

    if not inspector.has_table("sheet_parent_spec"):
        op.create_table(
            "sheet_parent_spec",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("width_mm", sa.Float(), nullable=False),
            sa.Column("height_mm", sa.Float(), nullable=False),
            sa.Column("material", sa.String(length=120), nullable=False),
            sa.Column("thickness", sa.String(length=80), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not inspector.has_table("sheet_cut_variant"):
        op.create_table(
            "sheet_cut_variant",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("parent_spec_id", sa.String(length=64), sa.ForeignKey("sheet_parent_spec.id"), nullable=False),
            sa.Column("variant_code", sa.String(length=120), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("width_mm", sa.Float(), nullable=False),
            sa.Column("height_mm", sa.Float(), nullable=False),
            sa.Column("cuts_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("waste_rate", sa.Float(), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_sheet_cut_variant_parent_kind", "sheet_cut_variant", ["parent_spec_id", "kind"])

    if not inspector.has_table("batch_layout_job"):
        op.create_table(
            "batch_layout_job",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("batch_id", sa.String(length=64), sa.ForeignKey("batch_upload.id"), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("moq_per_item", sa.Integer(), nullable=False),
            sa.Column("top_k", sa.Integer(), nullable=False),
            sa.Column("sheet_parent_spec_id", sa.String(length=64), sa.ForeignKey("sheet_parent_spec.id"), nullable=False),
            sa.Column("params_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("audit_manifest_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_batch_layout_job_batch_status", "batch_layout_job", ["batch_id", "status"])

    if not inspector.has_table("batch_layout_group"):
        op.create_table(
            "batch_layout_group",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("job_id", sa.String(length=64), sa.ForeignKey("batch_layout_job.id"), nullable=False),
            sa.Column("batch_id", sa.String(length=64), sa.ForeignKey("batch_upload.id"), nullable=False),
            sa.Column("compatibility_key", sa.String(length=500), nullable=False),
            sa.Column("material", sa.String(length=120), nullable=True),
            sa.Column("thickness", sa.String(length=80), nullable=True),
            sa.Column("print_method", sa.String(length=80), nullable=True),
            sa.Column("spot_color", sa.String(length=120), nullable=True),
            sa.Column("due_date", sa.String(length=40), nullable=True),
            sa.Column("category", sa.String(length=120), nullable=True),
            sa.Column("customer_id", sa.String(length=120), nullable=True),
            sa.Column("item_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("stats_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_batch_layout_group_job_key", "batch_layout_group", ["job_id", "compatibility_key"])

    if not inspector.has_table("production_pattern"):
        op.create_table(
            "production_pattern",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("job_id", sa.String(length=64), sa.ForeignKey("batch_layout_job.id"), nullable=False),
            sa.Column("group_id", sa.String(length=64), sa.ForeignKey("batch_layout_group.id"), nullable=True),
            sa.Column("cut_variant_id", sa.String(length=64), sa.ForeignKey("sheet_cut_variant.id"), nullable=True),
            sa.Column("pattern_type", sa.String(length=80), nullable=False),
            sa.Column("units_per_sheet", sa.Integer(), nullable=False),
            sa.Column("required_sheets", sa.Integer(), nullable=False),
            sa.Column("total_units", sa.Integer(), nullable=False),
            sa.Column("utilization_rate", sa.Float(), nullable=False),
            sa.Column("quantity_fulfillment_rate", sa.Float(), nullable=False),
            sa.Column("hard_rule_pass", sa.Boolean(), nullable=False),
            sa.Column("validator_report_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_production_pattern_job_group", "production_pattern", ["job_id", "group_id"])

    if not inspector.has_table("production_plan"):
        op.create_table(
            "production_plan",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("job_id", sa.String(length=64), sa.ForeignKey("batch_layout_job.id"), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("intent", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("utilization_rate", sa.Float(), nullable=False),
            sa.Column("risk_score", sa.Float(), nullable=False),
            sa.Column("runtime_score", sa.Float(), nullable=False),
            sa.Column("diversity_score", sa.Float(), nullable=False),
            sa.Column("total_sheets_used", sa.Integer(), nullable=False),
            sa.Column("quantity_fulfillment_rate", sa.Float(), nullable=False),
            sa.Column("hard_rule_pass", sa.Boolean(), nullable=False),
            sa.Column("export_ok", sa.Boolean(), nullable=False),
            sa.Column("validator_report_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("audit_manifest_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_production_plan_job_rank", "production_plan", ["job_id", "rank"])

    if not inspector.has_table("production_plan_approval"):
        op.create_table(
            "production_plan_approval",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("plan_id", sa.String(length=64), sa.ForeignKey("production_plan.id"), nullable=False),
            sa.Column("requested_by", sa.String(length=64), sa.ForeignKey("user_account.id"), nullable=False),
            sa.Column("decided_by", sa.String(length=64), sa.ForeignKey("user_account.id"), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("request_note", sa.Text(), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not inspector.has_table("production_plan_export"):
        op.create_table(
            "production_plan_export",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("plan_id", sa.String(length=64), sa.ForeignKey("production_plan.id"), nullable=False),
            sa.Column("export_type", sa.String(length=40), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("lifecycle_status", sa.String(length=40), nullable=False),
            sa.Column("storage_key", sa.String(length=500), nullable=False),
            sa.Column("checksum", sa.String(length=128), nullable=True),
            sa.Column("storage_backend", sa.String(length=40), nullable=True),
            sa.Column("storage_object_key", sa.String(length=500), nullable=True),
            sa.Column("storage_version_id", sa.String(length=255), nullable=True),
            sa.Column("storage_etag", sa.String(length=255), nullable=True),
            sa.Column("storage_size_bytes", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not inspector.has_table("production_plan_pattern"):
        op.create_table(
            "production_plan_pattern",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("plan_id", sa.String(length=64), sa.ForeignKey("production_plan.id"), nullable=False),
            sa.Column("pattern_id", sa.String(length=64), sa.ForeignKey("production_pattern.id"), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("sheets_used", sa.Integer(), nullable=False),
            sa.Column("produced_units", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_production_plan_pattern_plan_sequence", "production_plan_pattern", ["plan_id", "sequence"])

    if not inspector.has_table("batch_benchmark_run"):
        op.create_table(
            "batch_benchmark_run",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("job_id", sa.String(length=64), sa.ForeignKey("batch_layout_job.id"), nullable=True),
            sa.Column("benchmark_type", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("file_count", sa.Integer(), nullable=False),
            sa.Column("p95_runtime_ms", sa.Integer(), nullable=True),
            sa.Column("peak_rss_mb", sa.Float(), nullable=True),
            sa.Column("hard_rule_pass_rate", sa.Float(), nullable=False),
            sa.Column("quantity_fulfillment_rate", sa.Float(), nullable=False),
            sa.Column("topk_legal_rate", sa.Float(), nullable=False),
            sa.Column("avg_case_score", sa.Float(), nullable=False),
            sa.Column("metrics_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in [
        "batch_benchmark_run",
        "production_plan_pattern",
        "production_plan_export",
        "production_plan_approval",
        "production_plan",
        "production_pattern",
        "batch_layout_group",
        "batch_layout_job",
        "sheet_cut_variant",
        "sheet_parent_spec",
        "batch_artwork_item",
        "batch_upload",
    ]:
        if inspector.has_table(table_name):
            op.drop_table(table_name)
