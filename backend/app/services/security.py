from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.schemas import CurrentUser
from app.services import repository


bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str, *, iterations: int = 210_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def validate_password_policy(password: str) -> None:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    if len(password) > 128:
        raise ValueError("password must be at most 128 characters")
    if not any(char.isalpha() for char in password):
        raise ValueError("password must include at least one letter")
    if not any(char.isdigit() for char in password):
        raise ValueError("password must include at least one digit")


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = hashed_password.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = _b64decode(salt_b64)
        expected = _b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_access_token(user: CurrentUser) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.access_token_ttl_minutes * 60
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "roles": user.roles,
        "permissions": user.permissions,
        "exp": int(time.time()) + expires_in,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    header_part = _b64json(header)
    payload_part = _b64json(payload)
    signature = _sign(f"{header_part}.{payload_part}")
    return f"{header_part}.{payload_part}.{signature}", expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        header_part, payload_part, signature = token.split(".", 2)
        expected = _sign(f"{header_part}.{payload_part}")
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid token signature")
        payload = json.loads(_b64decode(payload_part))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("token expired")
        return payload
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token") from exc


def authenticate_user(db: Session, email: str, password: str) -> CurrentUser | None:
    row = repository.get_user_by_email(db, email)
    if not row or not row.is_active or not verify_password(password, row.hashed_password):
        return None
    return repository.current_user_from_db(db, row.id)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    payload = decode_access_token(credentials.credentials)
    try:
        user = repository.current_user_from_db(db, payload["sub"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or inactive") from exc
    request.state.current_user = user
    return user


def require_permission(permission: str):
    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if permission not in current_user.permissions and "*" not in current_user.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission: {permission}")
        return current_user

    return dependency


def require_any_permission(*permissions: str):
    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if "*" in current_user.permissions or any(permission in current_user.permissions for permission in permissions):
            return current_user
        expected = " or ".join(permissions)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission: {expected}")

    return dependency


def _sign(value: str) -> str:
    secret = get_settings().auth_secret_key.encode("utf-8")
    return _b64(hmac.new(secret, value.encode("utf-8"), hashlib.sha256).digest())


def _b64json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
