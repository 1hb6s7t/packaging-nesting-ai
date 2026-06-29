from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Point = tuple[float, float]
MessageChannel = Literal["in_app", "webhook", "email"]


class SolverName(str, Enum):
    rectpack = "RectpackSolver"
    ortools = "OrToolsSolver"
    packing_solver = "PackingSolver"
    sparrow = "SparrowSolver"
    phoenix = "MockPhoenixSolver"


class BBox(BaseModel):
    width: float = Field(ge=0)
    height: float = Field(ge=0)
    min_x: float = 0
    min_y: float = 0
    max_x: float = 0
    max_y: float = 0


class PolygonAsset(BaseModel):
    shape_id: str
    unit: str = "mm"
    outer: list[Point]
    holes: list[list[Point]] = Field(default_factory=list)
    bbox: BBox | None = None
    area: float | None = None
    perimeter: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("outer")
    @classmethod
    def validate_outer(cls, value: list[Point]) -> list[Point]:
        if len(value) < 3:
            raise ValueError("polygon outer ring must contain at least three points")
        return value


class SheetSpec(BaseModel):
    sheet_id: str
    name: str | None = None
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    margin_top: float = Field(default=0, ge=0)
    margin_right: float = Field(default=0, ge=0)
    margin_bottom: float = Field(default=0, ge=0)
    margin_left: float = Field(default=0, ge=0)
    gripper_mm: float = Field(default=0, ge=0)
    material: str
    thickness: str
    cost_per_sheet: float = Field(default=0, ge=0)


class ProductionOrderIn(BaseModel):
    order_id: str
    external_order_id: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    product_id: str | None = None
    product_name: str
    category: str | None = None
    is_repeat_order: bool = False
    quote_amount: float = Field(default=0, ge=0)
    contacted: bool = False
    due_date: date | None = None
    quantity: int = Field(default=1, ge=1)
    material: str
    thickness: str
    print_side: str | None = None
    print_method: str | None = None
    color_count: int | None = Field(default=None, ge=0)
    spot_color: str | None = None
    surface_finish: str | None = None
    artwork_file_id: str | None = None
    allowed_rotations: list[int] = Field(default_factory=lambda: [0, 90, 180, 270])
    allow_mirror: bool = False
    min_gap_mm: float = Field(default=3, ge=0)
    bleed_mm: float = Field(default=2, ge=0)
    priority_note: str | None = None


class ProductionOrder(ProductionOrderIn):
    model_config = ConfigDict(from_attributes=True)

    priority_score: float = 0
    source_type: str = "manual"


class OrderImportRequest(BaseModel):
    orders: list[ProductionOrderIn]


class OrderImportResult(BaseModel):
    imported_count: int
    rejected_count: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    orders: list[ProductionOrder] = Field(default_factory=list)


class PreflightRequest(BaseModel):
    filename: str
    content_type: str = "image/svg+xml"
    content: str | None = None


class PreflightReport(BaseModel):
    filename: str
    source_format: str
    can_parse_directly: bool
    requires_conversion: bool
    requires_manual_review: bool
    warnings: list[str] = Field(default_factory=list)
    detected_layers: list[str] = Field(default_factory=list)
    dimensions_mm: dict[str, float] | None = None


class FileConversionJobRead(BaseModel):
    id: str
    artwork_file_id: str
    source_format: str
    target_format: str
    status: Literal["queued", "completed", "failed", "manual_required", "skipped", "overdue"]
    log: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class FileConversionJobUpdate(BaseModel):
    status: Literal["queued", "completed", "failed", "manual_required", "skipped", "overdue"]
    log: str | None = Field(default=None, max_length=4000)


class ArtworkVersionRead(BaseModel):
    id: str
    artwork_file_id: str
    version: int
    normalized_storage_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class FileConversionSubmitRequest(BaseModel):
    callback_url: str | None = Field(default=None, max_length=500)
    callback_token: str | None = Field(default=None, min_length=8, max_length=200)
    rotate_callback_token: bool = False
    sla_minutes: int | None = Field(default=None, ge=1, le=1440)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileConversionSubmitResult(BaseModel):
    job: FileConversionJobRead
    status: Literal["submitted", "failed"]
    remote_status_code: int | None = None
    remote_response: dict[str, Any] = Field(default_factory=dict)
    message: str


class FileConversionSlaCheckRequest(BaseModel):
    sla_minutes: int | None = Field(default=None, ge=1, le=1440)
    notify: bool = True


class FileConversionSlaCheckResult(BaseModel):
    status: Literal["ok", "overdue"]
    checked_count: int = 0
    overdue_count: int = 0
    notification_count: int = 0
    overdue_jobs: list[FileConversionJobRead] = Field(default_factory=list)


class FileConversionResultRequest(BaseModel):
    status: Literal["completed", "failed"] = "completed"
    target_format: Literal["svg", "dxf"] | None = None
    content: str | None = None
    content_base64: str | None = None
    storage_key: str | None = Field(default=None, max_length=500)
    log: str | None = Field(default=None, max_length=4000)
    parse_polygon: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileConversionResultApplyResult(BaseModel):
    job: FileConversionJobRead
    artwork_version: ArtworkVersionRead | None = None
    polygon_storage_key: str | None = None
    polygon_count: int = 0
    message: str


class HardConstraint(BaseModel):
    name: str
    when: str
    action: Literal["reject"] = "reject"
    reason: str


class SoftScoreRule(BaseModel):
    name: str
    expression: str
    weight: float = Field(ge=0)


class RuleSet(BaseModel):
    ruleset_id: str = "packaging_default_v1"
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    soft_scores: list[SoftScoreRule] = Field(default_factory=list)


class RuleSetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    is_active: bool = True
    definition: RuleSet


class RuleSetRead(BaseModel):
    id: str
    name: str
    version: str
    is_active: bool
    definition: RuleSet
    created_at: str
    updated_at: str


class RuleExecutionLogRead(BaseModel):
    id: str
    rule_set_id: str | None = None
    order_id: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RuleDecision(BaseModel):
    accepted: bool
    reasons: list[str] = Field(default_factory=list)
    priority_score: float = 0
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    evaluation_errors: list[str] = Field(default_factory=list)


class NestingItem(BaseModel):
    item_id: str
    order_id: str
    polygon: PolygonAsset
    quantity: int = Field(default=1, ge=1)
    priority_score: float = Field(default=0, ge=0)
    allowed_rotations: list[int] = Field(default_factory=lambda: [0, 90, 180, 270])
    allow_mirror: bool = False
    min_gap_mm: float = Field(default=3, ge=0)
    bleed_mm: float = Field(default=2, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Objective(BaseModel):
    primary: str = "maximize_utilization"
    secondary: list[str] = Field(default_factory=lambda: ["maximize_priority", "minimize_runtime"])


class SolverConfig(BaseModel):
    solver_name: SolverName = SolverName.rectpack
    time_limit_sec: int = Field(default=120, ge=1)
    seed: int | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class SolverRegistryRead(BaseModel):
    id: str
    name: str
    version: str
    enabled: bool
    license_policy: Literal["open_source", "review_required", "commercial", "disabled"] = "review_required"
    created_at: str
    updated_at: str


class SolverRegistryUpdate(BaseModel):
    version: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    license_policy: Literal["open_source", "review_required", "commercial", "disabled"] | None = None


class NestingJob(BaseModel):
    job_id: str
    unit: str = "mm"
    sheet: SheetSpec
    fixed_items: list[NestingItem] = Field(default_factory=list)
    candidate_items: list[NestingItem]
    constraints: dict[str, Any] = Field(default_factory=dict)
    objective: Objective = Field(default_factory=Objective)
    time_limit_sec: int = Field(default=120, ge=1)
    top_k: int = Field(default=5, ge=1, le=20)
    solver_config: SolverConfig = Field(default_factory=SolverConfig)


class Placement(BaseModel):
    item_id: str
    order_id: str
    x: float
    y: float
    rotation: int = 0
    mirrored: bool = False
    width: float | None = None
    height: float | None = None
    polygon: PolygonAsset | None = None


class UnplacedItem(BaseModel):
    item_id: str
    order_id: str | None = None
    reason: str


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    item_ids: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    is_valid: bool
    overlap: bool = False
    out_of_bounds: bool = False
    gripper_conflict: bool = False
    min_gap_violation: bool = False
    rotation_invalid: bool = False
    issues: list[ValidationIssue] = Field(default_factory=list)


class SolutionScore(BaseModel):
    utilization_score: float = 0
    total_priority_score: float = 0
    quote_coverage_score: float = 0
    due_date_score: float = 0
    manufacturability_score: float = 1
    stability_score: float = 1
    penalty: float = 0
    total: float = 0


class NestingSolution(BaseModel):
    solution_id: str
    job_id: str
    solver: SolverName | str
    status: Literal["valid", "invalid", "partial", "failed", "candidate", "pending_approval", "approved", "rejected"] = (
        "candidate"
    )
    rank: int = 1
    runtime_ms: int = 0
    utilization_rate: float = 0
    waste_rate: float = 1
    placed_items: list[Placement] = Field(default_factory=list)
    unplaced_items: list[UnplacedItem] = Field(default_factory=list)
    validation_report: ValidationReport | None = None
    score: SolutionScore | None = None
    exports: dict[str, str] = Field(default_factory=dict)


class SolutionList(BaseModel):
    job_id: str
    solutions: list[NestingSolution]


class ApprovalRequestCreate(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)
    confirmation: str | None = Field(default=None, max_length=300)


class ConfirmationRequest(BaseModel):
    confirmation: str | None = Field(default=None, max_length=300)


class SolutionApprovalRead(BaseModel):
    id: str
    solution_id: str
    requested_by: str
    decided_by: str | None = None
    status: Literal["pending", "approved", "rejected"]
    request_note: str | None = None
    decision_note: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class SolutionExportRead(BaseModel):
    id: str
    solution_id: str
    export_type: Literal["pdf", "dxf"]
    version: int = 1
    lifecycle_status: Literal["active", "superseded", "archived"] = "active"
    retention_until: str | None = None
    superseded_by_export_id: str | None = None
    storage_key: str
    checksum: str | None = None
    storage_backend: str | None = None
    storage_object_key: str | None = None
    storage_version_id: str | None = None
    storage_etag: str | None = None
    storage_size_bytes: int | None = None
    status: str = "ready"
    download_path: str
    created_at: str
    updated_at: str


class SolutionExportArchiveRequest(BaseModel):
    solution_id: str | None = Field(default=None, max_length=120)
    dry_run: bool = False


class SolutionExportArchiveResult(BaseModel):
    status: Literal["completed", "dry_run"]
    dry_run: bool
    cutoff_at: str
    solution_id: str | None = None
    checked_count: int = 0
    archived_count: int = 0
    archived_exports: list[SolutionExportRead] = Field(default_factory=list)


class SolutionExportRecoveryItem(BaseModel):
    export_id: str
    export_type: Literal["pdf", "dxf"]
    version: int
    lifecycle_status: Literal["active", "superseded", "archived"]
    storage_key: str
    storage_backend: str
    bucket: str | None = None
    object_key: str
    expected_storage_version_id: str | None = None
    actual_storage_version_id: str | None = None
    expected_etag: str | None = None
    actual_etag: str | None = None
    storage_exists: bool
    size_bytes: int | None = None
    expected_checksum: str | None = None
    actual_checksum: str | None = None
    status: Literal["ok", "missing", "unreadable", "checksum_mismatch", "version_mismatch"]
    error: str | None = None


class SolutionExportRecoveryDrillRequest(BaseModel):
    include_archive_dry_run: bool = True


class SolutionExportRecoveryReport(BaseModel):
    solution_id: str
    generated_at: str
    status: Literal["passed", "failed"]
    checked_count: int = 0
    ok_count: int = 0
    missing_count: int = 0
    unreadable_count: int = 0
    checksum_mismatch_count: int = 0
    version_mismatch_count: int = 0
    archive_dry_run: SolutionExportArchiveResult | None = None
    items: list[SolutionExportRecoveryItem] = Field(default_factory=list)


class WorkTaskRead(BaseModel):
    id: str
    task_type: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "timed_out"]
    target_type: str
    target_id: str
    parent_task_id: str | None = None
    actor_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    attempt: int = 1
    max_attempts: int = 3
    timeout_sec: int | None = None
    cancel_requested: bool = False
    progress_percent: int = 0
    heartbeat_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str
    updated_at: str


class WorkTaskMetrics(BaseModel):
    total: int = 0
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    timed_out: int = 0
    active: int = 0
    stale_running: int = 0
    stale_after_sec: int
    oldest_queued_at: str | None = None


class ApiRouteMetrics(BaseModel):
    method: str
    route: str
    status_class: str
    count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0
    avg_duration_ms: float = 0
    max_duration_ms: float = 0


class ApiMetricsRead(BaseModel):
    total_requests: int = 0
    error_count: int = 0
    total_duration_ms: float = 0
    avg_duration_ms: float = 0
    routes: list[ApiRouteMetrics] = Field(default_factory=list)


class TaskAlertRuleOverride(BaseModel):
    active_threshold: int | None = Field(default=None, ge=0)
    queued_threshold: int | None = Field(default=None, ge=0)
    stale_running_threshold: int | None = Field(default=None, ge=0)
    failure_threshold: int | None = Field(default=None, ge=0)
    notify: bool = True
    push_external: bool = True


class TaskAlertRead(BaseModel):
    code: str
    severity: Literal["warning", "critical"]
    message: str
    actual: int
    threshold: int


class TaskAlertCheckResult(BaseModel):
    status: Literal["ok", "alerting"]
    metrics: WorkTaskMetrics
    alerts: list[TaskAlertRead] = Field(default_factory=list)
    notification_count: int = 0
    external_push: dict[str, Any] | None = None


class ScheduledMaintenanceRunRequest(BaseModel):
    archive_expired_exports: bool = True
    archive_dry_run: bool = False
    conversion_sla_check: bool = True
    conversion_sla_notify: bool = True
    task_alert_check: bool = True
    task_alert_notify: bool = True
    task_alert_push_external: bool = False


class ScheduledMaintenanceRunResult(BaseModel):
    status: Literal["ok", "attention"]
    generated_at: str
    enabled_checks: list[str] = Field(default_factory=list)
    export_archive: SolutionExportArchiveResult | None = None
    conversion_sla: FileConversionSlaCheckResult | None = None
    task_alerts: TaskAlertCheckResult | None = None


class NotificationRead(BaseModel):
    id: str
    user_id: str
    event_type: str
    title: str
    message: str
    target_type: str | None = None
    target_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    is_read: bool = False
    read_at: str | None = None
    created_at: str
    updated_at: str


class MessageTemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    event_type: str = Field(min_length=2, max_length=120)
    channel: MessageChannel = "in_app"
    title_template: str = Field(min_length=1, max_length=240)
    message_template: str = Field(min_length=1, max_length=4000)
    recipient_permission_code: str | None = Field(default=None, max_length=120)
    recipient_group_id: str | None = Field(default=None, max_length=64)
    escalation_permission_code: str | None = Field(default=None, max_length=120)
    escalation_group_id: str | None = Field(default=None, max_length=64)
    escalation_after_minutes: int | None = Field(default=None, ge=1)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    event_type: str | None = Field(default=None, min_length=2, max_length=120)
    channel: MessageChannel | None = None
    title_template: str | None = Field(default=None, min_length=1, max_length=240)
    message_template: str | None = Field(default=None, min_length=1, max_length=4000)
    recipient_permission_code: str | None = Field(default=None, max_length=120)
    recipient_group_id: str | None = Field(default=None, max_length=64)
    escalation_permission_code: str | None = Field(default=None, max_length=120)
    escalation_group_id: str | None = Field(default=None, max_length=64)
    escalation_after_minutes: int | None = Field(default=None, ge=1)
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None


class MessageTemplateRead(BaseModel):
    id: str
    name: str
    event_type: str
    channel: MessageChannel
    title_template: str
    message_template: str
    recipient_permission_code: str | None = None
    recipient_group_id: str | None = None
    escalation_permission_code: str | None = None
    escalation_group_id: str | None = None
    escalation_after_minutes: int | None = None
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class NotificationRecipientGroupCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    member_user_ids: list[str] = Field(default_factory=list)
    permission_codes: list[str] = Field(default_factory=list)
    department_codes: list[str] = Field(default_factory=list)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotificationRecipientGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    member_user_ids: list[str] | None = None
    permission_codes: list[str] | None = None
    department_codes: list[str] | None = None
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None


class NotificationRecipientGroupRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    member_user_ids: list[str] = Field(default_factory=list)
    permission_codes: list[str] = Field(default_factory=list)
    department_codes: list[str] = Field(default_factory=list)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    resolved_user_count: int = 0
    created_at: str
    updated_at: str


class MessageDispatchRequest(BaseModel):
    event_type: str = Field(min_length=2, max_length=120)
    context: dict[str, Any] = Field(default_factory=dict)
    target_type: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=120)
    default_title: str = Field(default="系统消息", max_length=240)
    default_message: str = Field(default="系统事件已触发", max_length=4000)
    recipient_permission_code: str | None = Field(default=None, max_length=120)
    recipient_group_id: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    channel_filter: list[MessageChannel] | None = None


class MessageDispatchRecord(BaseModel):
    template_id: str | None = None
    event_type: str
    channel: MessageChannel
    status: Literal["sent", "skipped", "failed"]
    recipient_count: int = 0
    notification_count: int = 0
    external_push: dict[str, Any] | None = None
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MessageDispatchResult(BaseModel):
    event_type: str
    status: Literal["sent", "skipped", "failed"]
    notification_count: int = 0
    dispatches: list[MessageDispatchRecord] = Field(default_factory=list)


class MessageDispatchLogRead(BaseModel):
    id: str
    template_id: str | None = None
    event_type: str
    channel: MessageChannel
    target_type: str | None = None
    target_id: str | None = None
    status: Literal["sent", "skipped", "failed"]
    recipient_count: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    updated_at: str


class AiToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class AiToolCallRequest(BaseModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AiToolCallResult(BaseModel):
    tool_name: str
    status: Literal["completed", "blocked", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)
    message: str
    safety: dict[str, Any] = Field(default_factory=dict)


class AiChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AiChatResponse(BaseModel):
    mode: str
    message: str
    available_tools: list[str]
    recommended_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    actor: str
    input: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CurrentUser(BaseModel):
    user_id: str
    email: str
    display_name: str
    org_unit_code: str | None = None
    org_unit_name: str | None = None
    job_title: str | None = None
    external_user_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class PermissionRead(BaseModel):
    id: str
    code: str
    description: str | None = None


class RoleRead(BaseModel):
    id: str
    name: str
    description: str | None = None
    permission_codes: list[str] = Field(default_factory=list)


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = None
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = None
    permission_codes: list[str] | None = None


class UserAccountRead(BaseModel):
    id: str
    email: str
    display_name: str
    is_active: bool
    org_unit_code: str | None = None
    org_unit_name: str | None = None
    job_title: str | None = None
    external_user_id: str | None = None
    roles: list[RoleRead] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class UserAccountCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    is_active: bool = True
    org_unit_code: str | None = Field(default=None, max_length=120)
    org_unit_name: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    external_user_id: str | None = Field(default=None, max_length=120)
    role_ids: list[str] = Field(default_factory=list)


class UserAccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=12, max_length=128)
    is_active: bool | None = None
    org_unit_code: str | None = Field(default=None, max_length=120)
    org_unit_name: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    external_user_id: str | None = Field(default=None, max_length=120)
    role_ids: list[str] | None = None


class ExternalSystemCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    system_type: Literal["crm", "mes", "erp", "solver", "other"]
    enabled: bool = False


class ExternalSystemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    system_type: Literal["crm", "mes", "erp", "solver", "other"] | None = None
    enabled: bool | None = None


class ExternalSystemRead(BaseModel):
    id: str
    name: str
    system_type: str
    enabled: bool
    created_at: str
    updated_at: str


class AdapterConfigCreate(BaseModel):
    adapter_type: str = Field(min_length=2, max_length=80)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class AdapterConfigRead(BaseModel):
    id: str
    external_system_id: str
    adapter_type: str
    version: int = 1
    is_active: bool = True
    validation_status: Literal["untested", "passed", "failed"] = "untested"
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class AdapterDictionarySignoffRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    approver_name: str | None = Field(default=None, max_length=120)
    accepted_unmapped_statuses: list[str] = Field(default_factory=list)
    confirmation: str = Field(min_length=1, max_length=200)


class AdapterDictionarySignoffResult(BaseModel):
    config_id: str
    external_system_id: str
    status: Literal["signed"]
    signed_by: str
    signed_at: str
    approver_name: str | None = None
    note: str | None = None
    dictionary_keys: list[str] = Field(default_factory=list)
    accepted_unmapped_statuses: list[str] = Field(default_factory=list)
    field_acceptance: AdapterFieldAcceptanceResult


class SyncTaskRead(BaseModel):
    id: str
    external_system_id: str
    task_type: str
    status: Literal["pending", "completed", "failed", "skipped"]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class WritebackLogRead(BaseModel):
    id: str
    external_system_id: str | None = None
    target_id: str | None = None
    status: Literal["completed", "failed", "skipped"]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ProductionScheduleEntryRead(BaseModel):
    id: str
    external_system_id: str
    sync_task_id: str | None = None
    external_id: str
    order_id: str | None = None
    job_id: str | None = None
    line_code: str | None = None
    machine_code: str | None = None
    workstation: str | None = None
    planned_start_at: str | None = None
    planned_end_at: str | None = None
    status: str | None = None
    quantity: float | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class InventorySnapshotRead(BaseModel):
    id: str
    external_system_id: str
    sync_task_id: str | None = None
    external_id: str
    material_code: str | None = None
    material_name: str | None = None
    batch_no: str | None = None
    warehouse_code: str | None = None
    status: str | None = None
    available_qty: float | None = None
    reserved_qty: float | None = None
    unit: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class MaterialAvailabilityItemRead(BaseModel):
    material: str
    required_qty: float
    available_qty: float
    reserved_qty: float
    net_available_qty: float
    shortage_qty: float
    unit: str | None = None
    status: Literal["ok", "shortage", "unknown"]
    order_ids: list[str] = Field(default_factory=list)
    inventory_snapshot_ids: list[str] = Field(default_factory=list)
    source_count: int = 0


class MaterialAvailabilityCheckResult(BaseModel):
    job_id: str
    overall_status: Literal["ready", "blocked", "unknown"]
    checked_at: str
    order_count: int = 0
    inventory_source_count: int = 0
    missing_order_ids: list[str] = Field(default_factory=list)
    items: list[MaterialAvailabilityItemRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DeliveryConfirmationRead(BaseModel):
    id: str
    external_system_id: str
    sync_task_id: str | None = None
    external_id: str
    order_id: str | None = None
    shipment_no: str | None = None
    carrier: str | None = None
    tracking_no: str | None = None
    status: str | None = None
    shipped_at: str | None = None
    delivered_at: str | None = None
    quantity: float | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ProductionScheduleReadinessItem(BaseModel):
    order_id: str
    required_qty: float
    scheduled_qty: float
    status: Literal["scheduled", "in_progress", "completed", "blocked", "missing", "unknown"]
    latest_status: str | None = None
    schedule_entry_ids: list[str] = Field(default_factory=list)
    planned_start_at: str | None = None
    planned_end_at: str | None = None
    line_code: str | None = None
    machine_code: str | None = None


class DeliveryClosureItem(BaseModel):
    order_id: str
    required_qty: float
    delivered_qty: float
    status: Literal["delivered", "partial", "missing", "blocked", "unknown"]
    latest_status: str | None = None
    delivery_confirmation_ids: list[str] = Field(default_factory=list)
    delivered_at: str | None = None
    shipment_no: str | None = None
    carrier: str | None = None
    tracking_no: str | None = None


class JobProductionReadinessResult(BaseModel):
    job_id: str
    overall_status: Literal["ready", "blocked", "unknown"]
    material_status: Literal["ready", "blocked", "unknown"]
    schedule_status: Literal["scheduled", "in_progress", "completed", "blocked", "missing", "unknown"]
    delivery_status: Literal["delivered", "partial", "missing", "blocked", "unknown"]
    checked_at: str
    order_count: int = 0
    schedule_source_count: int = 0
    delivery_source_count: int = 0
    material: MaterialAvailabilityCheckResult | None = None
    schedule_items: list[ProductionScheduleReadinessItem] = Field(default_factory=list)
    delivery_items: list[DeliveryClosureItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProductionAlertRuleOverride(BaseModel):
    notify: bool = True
    dedupe_minutes: int | None = Field(default=None, ge=1)


class ProductionAlertRead(BaseModel):
    code: str
    severity: Literal["warning", "critical"]
    message: str
    status: str
    affected_order_ids: list[str] = Field(default_factory=list)


class ProductionAlertCheckResult(BaseModel):
    status: Literal["ok", "alerting"]
    readiness: JobProductionReadinessResult
    alerts: list[ProductionAlertRead] = Field(default_factory=list)
    notification_count: int = 0


class ProcurementAlertRuleOverride(BaseModel):
    notify: bool = True
    dedupe_minutes: int | None = Field(default=None, ge=1)
    safety_stock_rate: float = Field(default=0, ge=0, le=1)
    min_purchase_qty: float = Field(default=0, ge=0)


class ProcurementRecommendationRead(BaseModel):
    material: str
    shortage_qty: float
    recommended_purchase_qty: float
    unit: str | None = None
    severity: Literal["warning", "critical"]
    order_ids: list[str] = Field(default_factory=list)
    inventory_snapshot_ids: list[str] = Field(default_factory=list)


class ProcurementAlertCheckResult(BaseModel):
    status: Literal["ok", "alerting"]
    material_readiness: MaterialAvailabilityCheckResult
    recommendations: list[ProcurementRecommendationRead] = Field(default_factory=list)
    notification_count: int = 0


class JobExceptionWritebackRequest(BaseModel):
    dry_run: bool = True
    include_procurement: bool = True
    include_schedule: bool = True
    include_delivery: bool = True
    safety_stock_rate: float = Field(default=0, ge=0, le=1)
    min_purchase_qty: float = Field(default=0, ge=0)


class JobExceptionWritebackAction(BaseModel):
    system_type: Literal["mes", "erp"]
    target_type: str
    requested_status: str
    reason: str
    writeback_log: WritebackLogRead


class JobExceptionWritebackResult(BaseModel):
    job_id: str
    dry_run: bool = True
    status: Literal["ok", "completed", "partial", "failed", "skipped"]
    action_count: int = 0
    writeback_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    readiness: JobProductionReadinessResult
    procurement_recommendations: list[ProcurementRecommendationRead] = Field(default_factory=list)
    actions: list[JobExceptionWritebackAction] = Field(default_factory=list)


class AdapterWritebackRequest(BaseModel):
    target_id: str | None = Field(default=None, max_length=120)
    target_type: str = Field(default="solution", max_length=80)
    status: str = Field(default="completed", max_length=80)
    dry_run: bool | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AdapterConnectionTestResult(BaseModel):
    config_id: str
    status: Literal["passed", "failed"]
    missing_fields: list[str] = Field(default_factory=list)
    message: str


class AdapterFieldAcceptanceCheck(BaseModel):
    scope: Literal["record", "mapping", "status", "writeback", "sample", "organization"]
    field: str
    required: bool = False
    status: Literal["passed", "warning", "failed"]
    source_path: str | None = None
    observed_count: int = 0
    missing_count: int = 0
    sample_values: list[str] = Field(default_factory=list)
    message: str


class AdapterFieldAcceptanceResult(BaseModel):
    config_id: str
    external_system_id: str
    system_type: str
    adapter_type: str
    adapter_version: int
    status: Literal["passed", "warning", "failed"]
    domain_target: str | None = None
    sample_count: int = 0
    required_missing_count: int = 0
    unresolved_mapping_count: int = 0
    unmapped_status_count: int = 0
    checks: list[AdapterFieldAcceptanceCheck] = Field(default_factory=list)
    message: str


class AdapterStatusRead(BaseModel):
    systems: list[ExternalSystemRead] = Field(default_factory=list)
    configs: list[AdapterConfigRead] = Field(default_factory=list)
    active_config_count: int = 0
    configured_system_count: int = 0
    enabled_system_count: int = 0


class AdapterReadinessCheck(BaseModel):
    code: str
    scope: str
    status: Literal["passed", "warning", "failed"]
    severity: Literal["info", "warning", "critical"] = "info"
    message: str
    target_type: str | None = None
    target_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class AdapterReadinessReport(BaseModel):
    status: Literal["ready", "warning", "blocked"]
    generated_at: str
    required_system_types: list[str] = Field(default_factory=list)
    passed_count: int = 0
    warning_count: int = 0
    failed_count: int = 0
    checks: list[AdapterReadinessCheck] = Field(default_factory=list)


class BenchmarkCase(BaseModel):
    case_id: str
    name: str
    sheet: SheetSpec
    items: list[NestingItem]
    baseline_utilization_rate: float | None = None


class BenchmarkCaseRead(BenchmarkCase):
    source: str = "synthetic"
    created_at: str
    updated_at: str


class BenchmarkRunResult(BaseModel):
    run_id: str | None = None
    case_id: str
    solver_name: str
    utilization_rate: float
    waste_rate: float
    runtime_ms: int
    valid: bool
    failure_reason: str | None = None
    created_at: str | None = None
