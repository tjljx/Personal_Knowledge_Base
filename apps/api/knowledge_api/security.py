from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.db import get_db
from knowledge_api.models import AuthSession, RevokedToken, User

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def issue_token(user: User, session_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user.id,
        "jti": str(uuid4()),
        "sid": session_id,
        "ver": user.auth_version,
        "iat": now,
        "exp": now + timedelta(minutes=get_settings().jwt_minutes),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> dict:
    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证无效")
    if credentials is None:
        raise error
    try:
        payload = jwt.decode(
            credentials.credentials, get_settings().jwt_secret, algorithms=["HS256"]
        )
        UUID(payload["sub"])
        UUID(payload["jti"])
        UUID(payload["sid"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise error from exc
    if db.get(RevokedToken, payload["jti"]) is not None:
        raise error
    auth_session = db.get(AuthSession, payload["sid"])
    if auth_session is None or auth_session.user_id != payload["sub"]:
        raise error
    last_activity = auth_session.last_activity_at
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=UTC)
    if datetime.now(UTC) - last_activity >= timedelta(minutes=get_settings().session_idle_minutes):
        raise error
    user = db.get(User, payload["sub"])
    if user is None or user.status != "ACTIVE" or payload.get("ver", 0) != user.auth_version:
        raise error
    return payload


def current_user(claims: dict = Depends(current_claims), db: Session = Depends(get_db)) -> User:
    user = db.get(User, claims["sub"])
    if user is None or user.status != "ACTIVE":
        raise HTTPException(status_code=401, detail="认证无效")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
