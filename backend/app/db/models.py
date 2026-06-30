from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class UserAccount(Base, TimestampMixin):
    __tablename__ = "user_account"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    org_unit_code: Mapped[str | None] = mapped_column(String(120))
    org_unit_name: Mapped[str | None] = mapped_column(String(120))
    job_title: Mapped[str | None] = mapped_column(String(120))
    external_user_id: Mapped[str | None] = mapped_column(String(120))


class Role(Base, TimestampMixin):
    __tablename__ = "role"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("role"))
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Permission(Base, TimestampMixin):
    __tablename__ = "permission"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("perm"))
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(Base, TimestampMixin):
    __tablename__ = "user_role"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("urole"))
    user_id: Mapped[str] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    role_id: Mapped[str] = mapped_column(ForeignKey("role.id"), nullable=False)


class RolePermission(Base, TimestampMixin):
    __tablename__ = "role_permission"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rperm"))
    role_id: Mapped[str] = mapped_column(ForeignKey("role.id"), nullable=False)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permission.id"), nullable=False)


class OperationLog(Base, TimestampMixin):
    __tablename__ = "operation_log"
    __table_args__ = (
        Index("ix_operation_log_created_at", "created_at"),
        Index("ix_operation_log_action_created_at", "action", "created_at"),
        Index("ix_operation_log_target_created_at", "target_type", "target_id", "created_at"),
        Index("ix_operation_log_actor_created_at", "actor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("oplog"))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Notification(Base, TimestampMixin):
    __tablename__ = "notification"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ntf"))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(120))
    target_id: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class MessageTemplate(Base, TimestampMixin):
    __tablename__ = "message_template"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg_tpl"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), default="in_app", nullable=False)
    title_template: Mapped[str] = mapped_column(String(240), nullable=False)
    message_template: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_permission_code: Mapped[str | None] = mapped_column(String(120))
    recipient_group_id: Mapped[str | None] = mapped_column(String(64))
    escalation_permission_code: Mapped[str | None] = mapped_column(String(120))
    escalation_group_id: Mapped[str | None] = mapped_column(String(64))
    escalation_after_minutes: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NotificationRecipientGroup(Base, TimestampMixin):
    __tablename__ = "notification_recipient_group"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rcpt_grp"))
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    member_user_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    permission_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    department_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MessageDispatchLog(Base, TimestampMixin):
    __tablename__ = "message_dispatch_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg_log"))
    template_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(120))
    target_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class WorkTask(Base, TimestampMixin):
    __tablename__ = "work_task"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("task"))
    parent_task_id: Mapped[str | None] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    target_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    timeout_sec: Mapped[int | None] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ArtworkFile(Base, TimestampMixin):
    __tablename__ = "artwork_file"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    source_format: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="uploaded", nullable=False)


class ArtworkVersion(Base, TimestampMixin):
    __tablename__ = "artwork_version"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("artv"))
    artwork_file_id: Mapped[str] = mapped_column(ForeignKey("artwork_file.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_storage_key: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class FileConversionJob(Base, TimestampMixin):
    __tablename__ = "file_conversion_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("conv"))
    artwork_file_id: Mapped[str] = mapped_column(ForeignKey("artwork_file.id"), nullable=False)
    source_format: Mapped[str] = mapped_column(String(32), nullable=False)
    target_format: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    log: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class FilePreflightReport(Base, TimestampMixin):
    __tablename__ = "file_preflight_report"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pre"))
    artwork_file_id: Mapped[str] = mapped_column(ForeignKey("artwork_file.id"), nullable=False)
    can_parse_directly: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_conversion: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BatchUpload(Base, TimestampMixin):
    __tablename__ = "batch_upload"
    __table_args__ = (
        Index("ix_batch_upload_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("batch"))
    source_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(40), default="uploaded", nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uploaded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    preflighted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parsed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversion_required_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BatchArtworkItem(Base, TimestampMixin):
    __tablename__ = "batch_artwork_item"
    __table_args__ = (
        Index("ix_batch_artwork_item_batch_status", "batch_id", "status"),
        Index("ix_batch_artwork_item_batch_classification", "batch_id", "classification"),
        Index("ix_batch_artwork_item_compatibility", "material", "thickness", "print_method", "spot_color"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("bitem"))
    batch_id: Mapped[str] = mapped_column(ForeignKey("batch_upload.id"), nullable=False)
    artwork_file_id: Mapped[str | None] = mapped_column(ForeignKey("artwork_file.id"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120))
    checksum: Mapped[str | None] = mapped_column(String(128))
    source_format: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="uploaded", nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    material: Mapped[str | None] = mapped_column(String(120))
    thickness: Mapped[str | None] = mapped_column(String(80))
    print_method: Mapped[str | None] = mapped_column(String(80))
    spot_color: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(120))
    customer_id: Mapped[str | None] = mapped_column(String(120))
    preflight_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    feature_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    classification: Mapped[str | None] = mapped_column(String(40))
    parse_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ShapeLayer(Base, TimestampMixin):
    __tablename__ = "shape_layer"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("layer"))
    artwork_file_id: Mapped[str] = mapped_column(ForeignKey("artwork_file.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    semantic_type: Mapped[str] = mapped_column(String(40), default="unknown", nullable=False)


class DielineShape(Base, TimestampMixin):
    __tablename__ = "dieline_shape"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("shape"))
    artwork_file_id: Mapped[str] = mapped_column(ForeignKey("artwork_file.id"), nullable=False)
    layer_id: Mapped[str | None] = mapped_column(ForeignKey("shape_layer.id"))
    shape_type: Mapped[str] = mapped_column(String(40), nullable=False)
    geometry: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class PolygonAsset(Base, TimestampMixin):
    __tablename__ = "polygon_asset"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("poly"))
    artwork_file_id: Mapped[str] = mapped_column(ForeignKey("artwork_file.id"), nullable=False)
    unit: Mapped[str] = mapped_column(String(12), default="mm", nullable=False)
    polygon_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    area: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    bbox_width: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    bbox_height: Mapped[float] = mapped_column(Float, default=0, nullable=False)


class SheetSpec(Base, TimestampMixin):
    __tablename__ = "sheet_spec"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sheet"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    width_mm: Mapped[float] = mapped_column(Float, nullable=False)
    height_mm: Mapped[float] = mapped_column(Float, nullable=False)
    margin_top_mm: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    margin_right_mm: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    margin_bottom_mm: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    margin_left_mm: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    gripper_mm: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    material: Mapped[str] = mapped_column(String(120), nullable=False)
    thickness: Mapped[str] = mapped_column(String(80), nullable=False)
    cost_per_sheet: Mapped[float] = mapped_column(Float, default=0, nullable=False)


class SheetParentSpec(Base, TimestampMixin):
    __tablename__ = "sheet_parent_spec"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("parent"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    width_mm: Mapped[float] = mapped_column(Float, nullable=False)
    height_mm: Mapped[float] = mapped_column(Float, nullable=False)
    material: Mapped[str] = mapped_column(String(120), nullable=False)
    thickness: Mapped[str] = mapped_column(String(80), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SheetCutVariant(Base, TimestampMixin):
    __tablename__ = "sheet_cut_variant"
    __table_args__ = (
        Index("ix_sheet_cut_variant_parent_kind", "parent_spec_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cut"))
    parent_spec_id: Mapped[str] = mapped_column(ForeignKey("sheet_parent_spec.id"), nullable=False)
    variant_code: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    width_mm: Mapped[float] = mapped_column(Float, nullable=False)
    height_mm: Mapped[float] = mapped_column(Float, nullable=False)
    cuts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    waste_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BatchLayoutJob(Base, TimestampMixin):
    __tablename__ = "batch_layout_job"
    __table_args__ = (
        Index("ix_batch_layout_job_batch_status", "batch_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("bljob"))
    batch_id: Mapped[str] = mapped_column(ForeignKey("batch_upload.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="created", nullable=False)
    moq_per_item: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    sheet_parent_spec_id: Mapped[str] = mapped_column(ForeignKey("sheet_parent_spec.id"), nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    audit_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BatchLayoutGroup(Base, TimestampMixin):
    __tablename__ = "batch_layout_group"
    __table_args__ = (
        Index("ix_batch_layout_group_job_key", "job_id", "compatibility_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("blgrp"))
    job_id: Mapped[str] = mapped_column(ForeignKey("batch_layout_job.id"), nullable=False)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batch_upload.id"), nullable=False)
    compatibility_key: Mapped[str] = mapped_column(String(500), nullable=False)
    material: Mapped[str | None] = mapped_column(String(120))
    thickness: Mapped[str | None] = mapped_column(String(80))
    print_method: Mapped[str | None] = mapped_column(String(80))
    spot_color: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(120))
    customer_id: Mapped[str | None] = mapped_column(String(120))
    item_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ProductionPattern(Base, TimestampMixin):
    __tablename__ = "production_pattern"
    __table_args__ = (
        Index("ix_production_pattern_job_group", "job_id", "group_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pat"))
    job_id: Mapped[str] = mapped_column(ForeignKey("batch_layout_job.id"), nullable=False)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("batch_layout_group.id"))
    cut_variant_id: Mapped[str | None] = mapped_column(ForeignKey("sheet_cut_variant.id"))
    pattern_type: Mapped[str] = mapped_column(String(80), nullable=False)
    units_per_sheet: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required_sheets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    utilization_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    quantity_fulfillment_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    hard_rule_pass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validator_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    placement_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    placement_svg: Mapped[str] = mapped_column(Text, default="", nullable=False)
    placement_checksum: Mapped[str | None] = mapped_column(String(128))
    placement_solver_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ProductionPlan(Base, TimestampMixin):
    __tablename__ = "production_plan"
    __table_args__ = (
        Index("ix_production_plan_job_rank", "job_id", "rank"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("plan"))
    job_id: Mapped[str] = mapped_column(ForeignKey("batch_layout_job.id"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    intent: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="candidate", nullable=False)
    utilization_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    runtime_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    diversity_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    total_sheets_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quantity_fulfillment_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    hard_rule_pass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    export_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validator_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    audit_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ProductionPlanApproval(Base, TimestampMixin):
    __tablename__ = "production_plan_approval"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pappr"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("production_plan.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("user_account.id"))
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    request_note: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ProductionPlanExport(Base, TimestampMixin):
    __tablename__ = "production_plan_export"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pexp"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("production_plan.id"), nullable=False)
    export_type: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    storage_backend: Mapped[str | None] = mapped_column(String(40))
    storage_object_key: Mapped[str | None] = mapped_column(String(500))
    storage_version_id: Mapped[str | None] = mapped_column(String(255))
    storage_etag: Mapped[str | None] = mapped_column(String(255))
    storage_size_bytes: Mapped[int | None] = mapped_column(Integer)


class ProductionPlanPattern(Base, TimestampMixin):
    __tablename__ = "production_plan_pattern"
    __table_args__ = (
        Index("ix_production_plan_pattern_plan_sequence", "plan_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ppat"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("production_plan.id"), nullable=False)
    pattern_id: Mapped[str] = mapped_column(ForeignKey("production_pattern.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sheets_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    produced_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BatchBenchmarkRun(Base, TimestampMixin):
    __tablename__ = "batch_benchmark_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("bbench"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("batch_layout_job.id"))
    benchmark_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="running", nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    p95_runtime_ms: Mapped[int | None] = mapped_column(Integer)
    peak_rss_mb: Mapped[float | None] = mapped_column(Float)
    hard_rule_pass_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    quantity_fulfillment_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    topk_legal_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    avg_case_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MaterialSpec(Base, TimestampMixin):
    __tablename__ = "material_spec"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("mat"))
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class PrintProcessSpec(Base, TimestampMixin):
    __tablename__ = "print_process_spec"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("proc"))
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Customer(Base, TimestampMixin):
    __tablename__ = "customer"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cus"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(120))


class Product(Base, TimestampMixin):
    __tablename__ = "product"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prd"))
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customer.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))


class ProductionOrder(Base, TimestampMixin):
    __tablename__ = "production_order"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ord"))
    external_order_id: Mapped[str | None] = mapped_column(String(120))
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customer.id"))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("product.id"))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    is_repeat_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quote_amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    contacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    due_date: Mapped[str | None] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    material: Mapped[str] = mapped_column(String(120), nullable=False)
    thickness: Mapped[str] = mapped_column(String(80), nullable=False)
    print_side: Mapped[str | None] = mapped_column(String(40))
    print_method: Mapped[str | None] = mapped_column(String(80))
    color_count: Mapped[int | None] = mapped_column(Integer)
    spot_color: Mapped[str | None] = mapped_column(String(120))
    surface_finish: Mapped[str | None] = mapped_column(String(120))
    artwork_file_id: Mapped[str | None] = mapped_column(ForeignKey("artwork_file.id"))
    allowed_rotations: Mapped[list[int]] = mapped_column(JSON, default=lambda: [0, 90, 180, 270], nullable=False)
    allow_mirror: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    min_gap_mm: Mapped[float] = mapped_column(Float, default=3, nullable=False)
    bleed_mm: Mapped[float] = mapped_column(Float, default=2, nullable=False)
    priority_note: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)


class OrderArtworkMapping(Base, TimestampMixin):
    __tablename__ = "order_artwork_mapping"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("oam"))
    order_id: Mapped[str] = mapped_column(ForeignKey("production_order.id"), nullable=False)
    artwork_file_id: Mapped[str] = mapped_column(ForeignKey("artwork_file.id"), nullable=False)


class RuleSet(Base, TimestampMixin):
    __tablename__ = "rule_set"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rules"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class RuleItem(Base, TimestampMixin):
    __tablename__ = "rule_item"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rule"))
    rule_set_id: Mapped[str] = mapped_column(ForeignKey("rule_set.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float | None] = mapped_column(Float)


class RuleExecutionLog(Base, TimestampMixin):
    __tablename__ = "rule_execution_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rlog"))
    rule_set_id: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NestingJob(Base, TimestampMixin):
    __tablename__ = "nesting_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("job"))
    status: Mapped[str] = mapped_column(String(40), default="created", nullable=False)
    sheet_spec_id: Mapped[str | None] = mapped_column(ForeignKey("sheet_spec.id"))
    rule_set_id: Mapped[str | None] = mapped_column(ForeignKey("rule_set.id"))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    objective: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    time_limit_sec: Mapped[int] = mapped_column(Integer, default=120, nullable=False)


class NestingJobItem(Base, TimestampMixin):
    __tablename__ = "nesting_job_item"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("jobitem"))
    nesting_job_id: Mapped[str] = mapped_column(ForeignKey("nesting_job.id"), nullable=False)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("production_order.id"))
    item_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="candidate", nullable=False)


class NestingJobConfig(Base, TimestampMixin):
    __tablename__ = "nesting_job_config"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("jobcfg"))
    nesting_job_id: Mapped[str] = mapped_column(ForeignKey("nesting_job.id"), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SolverRegistry(Base, TimestampMixin):
    __tablename__ = "solver_registry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("solver"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    license_policy: Mapped[str] = mapped_column(String(80), default="review_required", nullable=False)


class SolverRun(Base, TimestampMixin):
    __tablename__ = "solver_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    nesting_job_id: Mapped[str] = mapped_column(ForeignKey("nesting_job.id"), nullable=False)
    solver_name: Mapped[str] = mapped_column(String(120), nullable=False)
    solver_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="running", nullable=False)
    runtime_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SolverRunLog(Base, TimestampMixin):
    __tablename__ = "solver_run_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("runlog"))
    solver_run_id: Mapped[str] = mapped_column(ForeignKey("solver_run.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NestingSolution(Base, TimestampMixin):
    __tablename__ = "nesting_solution"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sol"))
    nesting_job_id: Mapped[str] = mapped_column(ForeignKey("nesting_job.id"), nullable=False)
    solver_run_id: Mapped[str | None] = mapped_column(ForeignKey("solver_run.id"))
    status: Mapped[str] = mapped_column(String(40), default="candidate", nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    utilization_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    waste_rate: Mapped[float] = mapped_column(Float, default=1, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    solution_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SolutionPlacement(Base, TimestampMixin):
    __tablename__ = "solution_placement"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("place"))
    solution_id: Mapped[str] = mapped_column(ForeignKey("nesting_solution.id"), nullable=False)
    item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(120))
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    rotation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mirrored: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    placement_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ValidationReport(Base, TimestampMixin):
    __tablename__ = "validation_report"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("val"))
    solution_id: Mapped[str] = mapped_column(ForeignKey("nesting_solution.id"), nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SolutionExport(Base, TimestampMixin):
    __tablename__ = "solution_export"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("exp"))
    solution_id: Mapped[str] = mapped_column(ForeignKey("nesting_solution.id"), nullable=False)
    export_type: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime)
    superseded_by_export_id: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    storage_backend: Mapped[str | None] = mapped_column(String(40))
    storage_object_key: Mapped[str | None] = mapped_column(String(500))
    storage_version_id: Mapped[str | None] = mapped_column(String(255))
    storage_etag: Mapped[str | None] = mapped_column(String(255))
    storage_size_bytes: Mapped[int | None] = mapped_column(Integer)


class SolutionApproval(Base, TimestampMixin):
    __tablename__ = "solution_approval"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("appr"))
    solution_id: Mapped[str] = mapped_column(ForeignKey("nesting_solution.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("user_account.id"))
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    request_note: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ExternalSystem(Base, TimestampMixin):
    __tablename__ = "external_system"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ext"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    system_type: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AdapterConfig(Base, TimestampMixin):
    __tablename__ = "adapter_config"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("adp"))
    external_system_id: Mapped[str] = mapped_column(ForeignKey("external_system.id"), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(40), default="untested", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SyncTask(Base, TimestampMixin):
    __tablename__ = "sync_task"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sync"))
    external_system_id: Mapped[str] = mapped_column(ForeignKey("external_system.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WritebackLog(Base, TimestampMixin):
    __tablename__ = "writeback_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("wblog"))
    external_system_id: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ProductionScheduleEntry(Base, TimestampMixin):
    __tablename__ = "production_schedule_entry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sched"))
    external_system_id: Mapped[str] = mapped_column(ForeignKey("external_system.id"), nullable=False)
    sync_task_id: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(120))
    job_id: Mapped[str | None] = mapped_column(String(120))
    line_code: Mapped[str | None] = mapped_column(String(120))
    machine_code: Mapped[str | None] = mapped_column(String(120))
    workstation: Mapped[str | None] = mapped_column(String(120))
    planned_start_at: Mapped[str | None] = mapped_column(String(80))
    planned_end_at: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[float | None] = mapped_column(Float)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class InventorySnapshot(Base, TimestampMixin):
    __tablename__ = "inventory_snapshot"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("inv"))
    external_system_id: Mapped[str] = mapped_column(ForeignKey("external_system.id"), nullable=False)
    sync_task_id: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    material_code: Mapped[str | None] = mapped_column(String(120))
    material_name: Mapped[str | None] = mapped_column(String(255))
    batch_no: Mapped[str | None] = mapped_column(String(120))
    warehouse_code: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(80))
    available_qty: Mapped[float | None] = mapped_column(Float)
    reserved_qty: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(40))
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class DeliveryConfirmation(Base, TimestampMixin):
    __tablename__ = "delivery_confirmation"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("deliv"))
    external_system_id: Mapped[str] = mapped_column(ForeignKey("external_system.id"), nullable=False)
    sync_task_id: Mapped[str | None] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(120))
    shipment_no: Mapped[str | None] = mapped_column(String(120))
    carrier: Mapped[str | None] = mapped_column(String(120))
    tracking_no: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(80))
    shipped_at: Mapped[str | None] = mapped_column(String(80))
    delivered_at: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[float | None] = mapped_column(Float)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class BenchmarkCase(Base, TimestampMixin):
    __tablename__ = "benchmark_case"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("bench"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(120), default="synthetic", nullable=False)
    case_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    baseline_utilization_rate: Mapped[float | None] = mapped_column(Float)


class BenchmarkRun(Base, TimestampMixin):
    __tablename__ = "benchmark_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("brun"))
    benchmark_case_id: Mapped[str] = mapped_column(ForeignKey("benchmark_case.id"), nullable=False)
    solver_name: Mapped[str] = mapped_column(String(120), nullable=False)
    planning_mode: Mapped[str] = mapped_column(String(40), default="single_sheet", nullable=False)
    utilization_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    waste_rate: Mapped[float] = mapped_column(Float, default=1, nullable=False)
    runtime_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hard_rule_pass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quantity_fulfillment_rate: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    requested_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    produced_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shortage_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overproduction_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    units_per_sheet: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sheets_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    peak_rss_mb: Mapped[float | None] = mapped_column(Float)
    export_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    case_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    baseline_delta_utilization_rate: Mapped[float | None] = mapped_column(Float)
    p95_runtime_ms: Mapped[int | None] = mapped_column(Integer)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
