from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from conftest import add_user, login
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.config import get_settings
from knowledge_api.models import AuditLog, AuthSession


def test_login_logout_and_authorization(client):
    http, engine = client
    add_user(engine, "admin", "ADMIN")
    add_user(engine, "member")

    assert http.get("/api/v1/users").status_code == 401
    member_headers = login(http, "member")
    assert http.get("/api/v1/users", headers=member_headers).status_code == 403

    admin_headers = login(http, "admin")
    response = http.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "new.member", "password": "another-strong-pass"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "MEMBER"
    assert "password_hash" not in response.json()

    assert http.post("/api/v1/auth/logout", headers=member_headers).status_code == 204
    assert http.get("/api/v1/auth/me", headers=member_headers).status_code == 401


def test_active_session_can_renew_token(client):
    http, engine = client
    add_user(engine, "member")
    headers = login(http, "member")
    assert http.post("/api/v1/auth/renew").status_code == 401
    renewed = http.post("/api/v1/auth/renew", headers=headers)
    assert renewed.status_code == 200
    refreshed_headers = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
    assert refreshed_headers != headers
    assert http.get("/api/v1/auth/me", headers=refreshed_headers).json()["username"] == "member"
    assert http.post("/api/v1/auth/logout", headers=refreshed_headers).status_code == 204
    assert http.get("/api/v1/auth/me", headers=refreshed_headers).status_code == 401


def test_idle_expiry_is_enforced_by_server_and_reads_do_not_extend_it(client):
    http, engine = client
    add_user(engine, "member")
    headers = login(http, "member")
    token = headers["Authorization"].split()[1]
    session_id = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])["sid"]

    with Session(engine) as db:
        auth_session = db.get(AuthSession, session_id)
        auth_session.last_activity_at = datetime.now(UTC) - timedelta(minutes=9)
        db.commit()
    assert http.get("/api/v1/auth/me", headers=headers).status_code == 200
    with Session(engine) as db:
        last_activity = db.get(AuthSession, session_id).last_activity_at
        assert datetime.now(UTC) - last_activity.replace(tzinfo=UTC) > timedelta(minutes=8)

    renewed = http.post("/api/v1/auth/renew", headers=headers)
    assert renewed.status_code == 200
    renewed_headers = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
    with Session(engine) as db:
        last_activity = db.get(AuthSession, session_id).last_activity_at
        assert datetime.now(UTC) - last_activity.replace(tzinfo=UTC) < timedelta(minutes=1)

    with Session(engine) as db:
        db.get(AuthSession, session_id).last_activity_at = datetime.now(UTC) - timedelta(minutes=10)
        db.commit()
    assert http.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert http.get("/api/v1/auth/me", headers=renewed_headers).status_code == 401
    assert http.post("/api/v1/auth/renew", headers=renewed_headers).status_code == 401
    assert http.get("/api/v1/knowledge-bases", headers=renewed_headers).status_code == 401

    login(http, "member")
    with Session(engine) as db:
        assert db.get(AuthSession, session_id) is None


def test_logout_invalidates_earlier_renewed_tokens(client):
    http, engine = client
    add_user(engine, "member")
    headers = login(http, "member")
    renewed = http.post("/api/v1/auth/renew", headers=headers)
    renewed_headers = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
    assert http.post("/api/v1/auth/logout", headers=renewed_headers).status_code == 204
    assert http.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert http.get("/api/v1/auth/me", headers=renewed_headers).status_code == 401


def test_password_change_requires_current_password_and_revokes_existing_sessions(client):
    http, engine = client
    user_id = add_user(engine, "member")
    first_session = login(http, "member")
    second_session = login(http, "member")
    endpoint = "/api/v1/auth/change-password"

    assert (
        http.post(
            endpoint,
            json={"current_password": "correct-password-123", "new_password": "new"},
            headers=first_session,
        ).status_code
        == 422
    )
    assert (
        http.post(
            endpoint,
            json={"current_password": "wrong", "new_password": "new-password-456"},
            headers=first_session,
        ).status_code
        == 400
    )
    assert (
        http.post(
            endpoint,
            json={
                "current_password": "correct-password-123",
                "new_password": "correct-password-123",
            },
            headers=first_session,
        ).status_code
        == 400
    )
    assert (
        http.post(
            endpoint,
            json={"current_password": "correct-password-123", "new_password": "new-password-456"},
            headers=first_session,
        ).status_code
        == 204
    )

    assert http.get("/api/v1/auth/me", headers=first_session).status_code == 401
    assert http.get("/api/v1/auth/me", headers=second_session).status_code == 401
    assert (
        http.post(
            "/api/v1/auth/login", json={"username": "member", "password": "correct-password-123"}
        ).status_code
        == 401
    )
    new_login = http.post(
        "/api/v1/auth/login", json={"username": "member", "password": "new-password-456"}
    )
    assert new_login.status_code == 200
    with Session(engine) as db:
        assert (
            db.scalar(
                select(AuditLog).where(
                    AuditLog.user_id == user_id, AuditLog.action == "change_password"
                )
            )
            is not None
        )


def test_knowledge_base_isolation_and_membership(client):
    http, engine = client
    add_user(engine, "owner")
    other_id = add_user(engine, "other")
    owner_headers = login(http, "owner")
    other_headers = login(http, "other")

    created = http.post(
        "/api/v1/knowledge-bases",
        headers=owner_headers,
        json={"name": "Private", "description": "Internal"},
    )
    assert created.status_code == 201
    kb_id = created.json()["id"]
    assert http.get("/api/v1/knowledge-bases", headers=other_headers).json() == []
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}", headers=other_headers).status_code == 404

    grant = http.post(
        f"/api/v1/knowledge-bases/{kb_id}/members",
        headers=owner_headers,
        json={"user_id": other_id, "member_role": "VIEWER"},
    )
    assert grant.status_code == 201
    assert len(http.get("/api/v1/knowledge-bases", headers=other_headers).json()) == 1
    assert http.get(f"/api/v1/knowledge-bases/{kb_id}", headers=other_headers).status_code == 200
    assert (
        http.post(
            f"/api/v1/knowledge-bases/{kb_id}/members",
            headers=other_headers,
            json={"user_id": str(uuid4())},
        ).status_code
        == 403
    )


def test_failed_login_is_rate_limited(client):
    http, engine = client
    add_user(engine, "admin", "ADMIN")
    for _ in range(5):
        assert (
            http.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "bad"}
            ).status_code
            == 401
        )
    assert (
        http.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "correct-password-123"}
        ).status_code
        == 429
    )
