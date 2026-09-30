"""Correcting an excessive tariff must preserve unknown calls and audit the change."""

from conftest import add_user, login
from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.models import AuditLog, CloudBudgetMonth, CloudModelUsage
from knowledge_api.reprice_usage import reprice_flash_month


def test_reprice_flash_month_is_audited_and_idempotent(client):
    http, engine = client
    user_id = add_user(engine, "owner")
    headers = login(http, "owner")
    kb_id = http.post("/api/v1/knowledge-bases", headers=headers, json={"name": "Budget"}).json()[
        "id"
    ]
    with Session(engine) as db:
        db.add(CloudBudgetMonth(month="2026-09", spent_micro_cny=13000, reserved_micro_cny=0))
        db.add_all(
            [
                CloudModelUsage(
                    month="2026-09",
                    user_id=user_id,
                    knowledge_base_id=kb_id,
                    task="graph_extraction",
                    provider="deepseek",
                    model="deepseek-flash",
                    status="SUCCEEDED",
                    prompt_tokens=100,
                    completion_tokens=20,
                    reserved_micro_cny=20000,
                    charged_micro_cny=12000,
                ),
                CloudModelUsage(
                    month="2026-09",
                    user_id=user_id,
                    knowledge_base_id=kb_id,
                    task="knowledge_qa",
                    provider="deepseek",
                    model="deepseek-flash",
                    status="UNKNOWN",
                    reserved_micro_cny=1000,
                    charged_micro_cny=1000,
                ),
            ]
        )
        db.commit()

    with Session(engine) as db:
        preview = reprice_flash_month(db, "2026-09")
        assert preview["calls"] == 1
        assert db.get(CloudBudgetMonth, "2026-09").spent_micro_cny == 13000
        applied = reprice_flash_month(db, "2026-09", apply=True)
        assert applied["budget_after_cny"] == 0.00136
        assert db.get(CloudBudgetMonth, "2026-09").spent_micro_cny == 1360
        usages = db.scalars(select(CloudModelUsage).order_by(CloudModelUsage.status)).all()
        assert sorted(u.charged_micro_cny for u in usages) == [360, 1000]
        assert db.scalar(select(AuditLog).where(AuditLog.action == "reprice_cloud_usage"))
        assert reprice_flash_month(db, "2026-09", apply=True)["calls"] == 0
        assert db.get(CloudBudgetMonth, "2026-09").spent_micro_cny == 1360
