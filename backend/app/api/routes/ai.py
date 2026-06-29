from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import AiChatRequest, AiChatResponse, AiToolCallRequest, AiToolCallResult, AiToolDefinition, CurrentUser
from app.services import repository
from app.services.ai_tools import AI_TOOL_DEFINITIONS, execute_ai_tool, plan_ai_tool_calls
from app.services.security import require_permission

router = APIRouter()


@router.get("/tools", response_model=list[AiToolDefinition])
def list_ai_tools() -> list[AiToolDefinition]:
    return AI_TOOL_DEFINITIONS


@router.post("/chat", response_model=AiChatResponse)
def chat(
    payload: AiChatRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiChatResponse:
    recommended = plan_ai_tool_calls(payload.message)
    repository.log_operation(
        db,
        action="ai.chat.plan",
        target_type="ai_assistant",
        actor_id=current_user.user_id,
        payload={"message_length": len(payload.message), "recommended_tools": [item["tool_name"] for item in recommended]},
    )
    return AiChatResponse(
        mode="tool_calling_only",
        message=(
            "AI Assistant can only plan or execute controlled backend tools. "
            "It must not invent production coordinates or bypass Validator, approval, export, or adapter workflows."
        ),
        available_tools=[tool.name for tool in AI_TOOL_DEFINITIONS],
        recommended_tool_calls=recommended,
        actor=current_user.email,
        input=payload.model_dump(mode="json"),
    )


@router.post("/tools/execute", response_model=AiToolCallResult)
def execute_tool(
    payload: AiToolCallRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name=payload.tool_name, arguments=payload.arguments, actor_id=current_user.user_id)


@router.post("/tools/search-orders", response_model=AiToolCallResult)
def search_orders(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="search_orders", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/create-job", response_model=AiToolCallResult)
def create_job(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="create_nesting_job", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/run-solver", response_model=AiToolCallResult)
def run_solver(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="run_solver", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/validate-solution", response_model=AiToolCallResult)
def validate_solution(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="validate_solution", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/compare-solutions", response_model=AiToolCallResult)
def compare_solutions(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="compare_solutions", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/explain-unplaced-items", response_model=AiToolCallResult)
def explain_unplaced_items(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="explain_unplaced_items", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/generate-report", response_model=AiToolCallResult)
def generate_report(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="generate_report", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/export-pdf", response_model=AiToolCallResult)
def export_pdf(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="export_pdf", arguments=payload, actor_id=current_user.user_id)


@router.post("/tools/export-dxf", response_model=AiToolCallResult)
def export_dxf(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("ai:use")),
) -> AiToolCallResult:
    return execute_ai_tool(db, tool_name="export_dxf", arguments=payload, actor_id=current_user.user_id)
