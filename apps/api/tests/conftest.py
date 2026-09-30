import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-only-secret-with-at-least-32-characters"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from knowledge_api.db import Base, get_db
from knowledge_api.main import app
from knowledge_api.models import User
from knowledge_api.security import hash_password


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def test_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as test_client:
        yield test_client, engine
    app.dependency_overrides.clear()
    engine.dispose()


def add_user(engine, username: str, role: str = "MEMBER"):
    with Session(engine) as db:
        user = User(
            username=username, password_hash=hash_password("correct-password-123"), role=role
        )
        db.add(user)
        db.commit()
        return user.id


def login(client: TestClient, username: str):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
