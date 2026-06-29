import hashlib
import math
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.schemas import AuthToken, CurrentUser, LoginRequest
from app.services import repository
from app.services.security import authenticate_user, create_access_token, get_current_user

router = APIRouter()
_login_failures: dict[str, list[float]] = {}


@router.post("/login", response_model=AuthToken)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> AuthToken:
    client_host = _client_host(request)
    throttle_key = _login_throttle_key(payload.email, client_host)
    throttled, retry_after = _login_is_throttled(throttle_key)
    if throttled:
        _log_login_attempt(
            db,
            action="auth.login_throttled",
            email=payload.email,
            client_host=client_host,
            failure_count=len(_login_failures.get(throttle_key, [])),
            retry_after_sec=retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed login attempts; retry later",
            headers={"Retry-After": str(retry_after)},
        )
    user = authenticate_user(db, payload.email, payload.password)
    if user is None:
        failure_count = _record_login_failure(throttle_key)
        _log_login_attempt(
            db,
            action="auth.login_failed",
            email=payload.email,
            client_host=client_host,
            failure_count=failure_count,
            retry_after_sec=0,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
    _clear_login_failures(throttle_key)
    token, expires_in = create_access_token(user)
    repository.log_operation(
        db,
        action="auth.login",
        target_type="user_account",
        target_id=user.user_id,
        actor_id=user.user_id,
        payload={"email": user.email},
    )
    return AuthToken(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=CurrentUser)
def me(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return current_user


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_throttle_key(email: str, client_host: str) -> str:
    normalized_email = email.strip().lower()
    return hashlib.sha256(f"{normalized_email}|{client_host}".encode("utf-8")).hexdigest()


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def _login_is_throttled(key: str) -> tuple[bool, int]:
    settings = get_settings()
    now = time.monotonic()
    failures = _recent_login_failures(key, now)
    if len(failures) < settings.login_rate_limit_max_failures:
        return False, 0
    oldest = failures[0]
    retry_after = max(1, math.ceil(settings.login_rate_limit_window_sec - (now - oldest)))
    return True, retry_after


def _record_login_failure(key: str) -> int:
    now = time.monotonic()
    failures = _recent_login_failures(key, now)
    failures.append(now)
    _login_failures[key] = failures
    return len(failures)


def _recent_login_failures(key: str, now: float) -> list[float]:
    settings = get_settings()
    cutoff = now - settings.login_rate_limit_window_sec
    failures = [timestamp for timestamp in _login_failures.get(key, []) if timestamp >= cutoff]
    if failures:
        _login_failures[key] = failures
    else:
        _login_failures.pop(key, None)
    return failures


def _clear_login_failures(key: str) -> None:
    _login_failures.pop(key, None)


def _log_login_attempt(
    db: Session,
    *,
    action: str,
    email: str,
    client_host: str,
    failure_count: int,
    retry_after_sec: int,
) -> None:
    settings = get_settings()
    repository.log_operation(
        db,
        action=action,
        target_type="auth",
        target_id=_email_hash(email),
        actor_id=None,
        payload={
            "email_hash": _email_hash(email),
            "client_host": client_host,
            "failure_count": failure_count,
            "limit": settings.login_rate_limit_max_failures,
            "window_sec": settings.login_rate_limit_window_sec,
            "retry_after_sec": retry_after_sec,
        },
    )
