"""Cloud calls must be reserved, settled and stopped at the monthly ceiling."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import add_user, login
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.cloud_budget import reconcile_stale_calls, reserve_call
from knowledge_api.config import get_settings
from knowledge_api.model_gateway import ModelAnswer, ModelGatewayError, Source, answer_with_sources
from knowledge_api.models import CloudBudgetMonth, CloudModelUsage
from knowledge_api.worker import process_next


def test_cloud_budget_tracks_usage_and_blocks_next_call(client, tmp_path, monkeypatch):
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "model_input_cny_per_million", Decimal(100))
    monkeypatch.setattr(settings, "model_output_cny_per_million", Decimal(100))
    monkeypatch.setattr(settings, "cloud_monthly_budget_cny", Decimal(50))
    add_user(engine, "admin", role="ADMIN")
    add_user(engine, "member")
    admin = login(http, "admin")
    member = login(http, "member")
    kb_id = http.post("/api/v1/knowledge-bases", headers=admin, json={"name": "KB"}).json()["id"]
    assert (
        http.get(f"/api/v1/knowledge-bases/{kb_id}", headers=admin).json()["cloud_enabled"] is False
    )
    http.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        headers=admin,
        files={"file": ("memo.md", b"# Policy\n\nTravel invoice is required.")},
    )
    with Session(engine) as db:
        assert process_next(db)
    endpoint = f"/api/v1/knowledge-bases/{kb_id}/ask"
    assert (
        http.post(endpoint, headers=admin, json={"question": "Travel invoice"}).status_code == 403
    )
    assert (
        http.patch(
            f"/api/v1/knowledge-bases/{kb_id}/cloud-access",
            headers=member,
            json={"enabled": True},
        ).status_code
        == 403
    )
    assert (
        http.patch(
            f"/api/v1/knowledge-bases/{kb_id}/cloud-access",
            headers=admin,
            json={"enabled": True},
        ).json()["cloud_enabled"]
        is True
    )
    calls = []

    def answer(*args, **kwargs):
        calls.append(1)
        return ModelAnswer("Invoice required [1]", 50, 20)

    monkeypatch.setattr("knowledge_api.main.answer_with_sources", answer)
    first = http.post(endpoint, headers=admin, json={"question": "Travel invoice"})
    assert first.status_code == 200
    assert len(calls) == 1
    with Session(engine) as db:
        usage = db.scalars(select(CloudModelUsage)).one()
        month = db.get(CloudBudgetMonth, usage.month)
        assert usage.status == "SUCCEEDED"
        assert (usage.prompt_tokens, usage.completion_tokens) == (50, 20)
        assert usage.charged_micro_cny == 7000
        assert month.reserved_micro_cny == 0
        assert month.spent_micro_cny == 7000
        reservation = usage.reserved_micro_cny
    summary = http.get("/api/v1/model-usage/monthly", headers=admin)
    assert summary.status_code == 200
    assert summary.json()["charged_cny"] == 0.007
    monkeypatch.setattr(settings, "cloud_monthly_budget_cny", Decimal(reservation) / 1_000_000)
    blocked = http.post(endpoint, headers=admin, json={"question": "Travel invoice"})
    assert blocked.status_code == 429
    assert len(calls) == 1

    monkeypatch.setattr(settings, "cloud_monthly_budget_cny", Decimal(50))

    def fail(*args, **kwargs):
        raise ModelGatewayError("temporary failure")

    monkeypatch.setattr("knowledge_api.main.answer_with_sources", fail)
    assert (
        http.post(endpoint, headers=admin, json={"question": "Travel invoice"}).status_code == 502
    )
    with Session(engine) as db:
        usages = db.scalars(select(CloudModelUsage).order_by(CloudModelUsage.created_at)).all()
        assert len(usages) == 2
        assert usages[-1].status == "UNKNOWN"
        assert usages[-1].charged_micro_cny == usages[-1].reserved_micro_cny


def test_gateway_reads_provider_usage_and_caps_output(monkeypatch):
    settings = get_settings().model_copy(
        update={"model_api_key": SecretStr("test-key"), "model_max_output_tokens": 512}
    )
    captured = {}

    class Response:
        is_error = False

        def json(self):
            return {
                "choices": [{"message": {"content": "Answer [1]"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25},
            }

    def fake_post(url, headers, json, timeout):
        captured["body"] = json
        return Response()

    monkeypatch.setattr("knowledge_api.model_gateway.httpx.post", fake_post)
    result = answer_with_sources(settings, "Question", [Source(1, "Evidence")])
    assert result == ModelAnswer("Answer [1]", 100, 25)
    assert captured["body"]["max_tokens"] == 512


def test_abandoned_cloud_reservation_is_settled(client, monkeypatch):
    http, engine = client
    settings = get_settings()
    monkeypatch.setattr(settings, "model_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "model_input_cny_per_million", Decimal(1))
    monkeypatch.setattr(settings, "model_output_cny_per_million", Decimal(1))
    user_id = add_user(engine, "owner")
    headers = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=headers, json={"name": "Budget"}).json()[
        "id"
    ]
    with Session(engine) as db:
        usage = reserve_call(
            db, settings, user_id, kb_id, "Question", [Source(1, "Evidence")], None
        )
        reserved = usage.reserved_micro_cny
        usage.created_at = datetime.now(UTC) - timedelta(minutes=16)
        db.commit()
        assert reconcile_stale_calls(db, settings)
        assert not reconcile_stale_calls(db, settings)
        db.refresh(usage)
        month = db.get(CloudBudgetMonth, usage.month)
        assert usage.status == "UNKNOWN"
        assert usage.charged_micro_cny == reserved
        assert month.reserved_micro_cny == 0
        assert month.spent_micro_cny == reserved
