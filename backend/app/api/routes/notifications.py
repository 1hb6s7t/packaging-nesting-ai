from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.schemas import (
    CurrentUser,
    MessageDispatchLogRead,
    MessageDispatchRequest,
    MessageDispatchResult,
    MessageTemplateCreate,
    MessageTemplateRead,
    MessageTemplateUpdate,
    NotificationRecipientGroupCreate,
    NotificationRecipientGroupRead,
    NotificationRecipientGroupUpdate,
    NotificationRead,
)
from app.services import repository
from app.services.messaging import dispatch_message_event
from app.services.security import get_current_user, require_permission

router = APIRouter()


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[NotificationRead]:
    return repository.list_notifications(db, current_user.user_id, unread_only=unread_only, limit=limit)


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return {"updated_count": repository.mark_all_notifications_read(db, current_user.user_id)}


@router.get("/templates", response_model=list[MessageTemplateRead])
def list_message_templates(
    event_type: str | None = None,
    active_only: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> list[MessageTemplateRead]:
    return repository.list_message_templates(db, event_type=event_type, active_only=active_only, limit=limit)


@router.post("/templates", response_model=MessageTemplateRead)
def create_message_template(
    payload: MessageTemplateCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> MessageTemplateRead:
    try:
        template = repository.create_message_template(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repository.log_operation(
        db,
        actor_id=current_user.user_id,
        action="message_template.create",
        target_type="message_template",
        target_id=template.id,
        payload={"event_type": template.event_type, "channel": template.channel},
    )
    return template


@router.patch("/templates/{template_id}", response_model=MessageTemplateRead)
def update_message_template(
    template_id: str,
    payload: MessageTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> MessageTemplateRead:
    try:
        template = repository.update_message_template(db, template_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if template is None:
        raise HTTPException(status_code=404, detail="message template not found")
    repository.log_operation(
        db,
        actor_id=current_user.user_id,
        action="message_template.update",
        target_type="message_template",
        target_id=template.id,
        payload={"event_type": template.event_type, "channel": template.channel, "is_active": template.is_active},
    )
    return template


@router.post("/dispatch", response_model=MessageDispatchResult)
def dispatch_message(
    payload: MessageDispatchRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> MessageDispatchResult:
    result = dispatch_message_event(
        db,
        event_type=payload.event_type,
        context=payload.context,
        default_title=payload.default_title,
        default_message=payload.default_message,
        target_type=payload.target_type,
        target_id=payload.target_id,
        recipient_permission_code=payload.recipient_permission_code,
        recipient_group_id=payload.recipient_group_id,
        payload=payload.payload,
        channel_filter=set(payload.channel_filter) if payload.channel_filter else None,
        settings=get_settings(),
    )
    repository.log_operation(
        db,
        actor_id=current_user.user_id,
        action="message.dispatch",
        target_type=payload.target_type or "message_event",
        target_id=payload.target_id or payload.event_type,
        payload={
            "event_type": payload.event_type,
            "status": result.status,
            "notification_count": result.notification_count,
            "dispatch_count": len(result.dispatches),
        },
    )
    return result


@router.get("/recipient-groups", response_model=list[NotificationRecipientGroupRead])
def list_recipient_groups(
    active_only: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> list[NotificationRecipientGroupRead]:
    return repository.list_notification_recipient_groups(db, active_only=active_only, limit=limit)


@router.post("/recipient-groups", response_model=NotificationRecipientGroupRead)
def create_recipient_group(
    payload: NotificationRecipientGroupCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> NotificationRecipientGroupRead:
    try:
        group = repository.create_notification_recipient_group(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repository.log_operation(
        db,
        actor_id=current_user.user_id,
        action="notification_recipient_group.create",
        target_type="notification_recipient_group",
        target_id=group.id,
        payload={
            "name": group.name,
            "member_count": len(group.member_user_ids),
            "permission_codes": group.permission_codes,
            "department_codes": group.department_codes,
        },
    )
    return group


@router.patch("/recipient-groups/{group_id}", response_model=NotificationRecipientGroupRead)
def update_recipient_group(
    group_id: str,
    payload: NotificationRecipientGroupUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> NotificationRecipientGroupRead:
    try:
        group = repository.update_notification_recipient_group(db, group_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if group is None:
        raise HTTPException(status_code=404, detail="recipient group not found")
    repository.log_operation(
        db,
        actor_id=current_user.user_id,
        action="notification_recipient_group.update",
        target_type="notification_recipient_group",
        target_id=group.id,
        payload={"name": group.name, "is_active": group.is_active, "resolved_user_count": group.resolved_user_count},
    )
    return group


@router.get("/dispatch-logs", response_model=list[MessageDispatchLogRead])
def list_message_dispatch_logs(
    event_type: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("notifications:manage")),
) -> list[MessageDispatchLogRead]:
    return repository.list_message_dispatch_logs(db, event_type=event_type, limit=limit)


@router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> NotificationRead:
    notification = repository.mark_notification_read(db, notification_id, current_user.user_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="notification not found")
    return notification
