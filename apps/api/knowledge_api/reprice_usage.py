"""Reprice historical DeepSeek Flash estimates after correcting a deployment tariff."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from knowledge_api.cloud_budget import micro_cny
from knowledge_api.db import get_engine
from knowledge_api.models import AuditLog, CloudBudgetMonth, CloudModelUsage

OLD_INPUT_RATE = Decimal(100)
OLD_OUTPUT_RATE = Decimal(100)
NEW_INPUT_RATE = Decimal(2)
NEW_OUTPUT_RATE = Decimal(8)


def reprice_flash_month(db: Session, month: str, apply: bool = False) -> dict:
    budget = db.scalar(
        select(CloudBudgetMonth).where(CloudBudgetMonth.month == month).with_for_update()
    )
    if budget is None:
        raise ValueError(f"No cloud budget record for {month}")
    rows = db.scalars(
        select(CloudModelUsage)
        .where(
            CloudModelUsage.month == month,
            CloudModelUsage.provider == "deepseek",
            CloudModelUsage.model == "deepseek-flash",
            CloudModelUsage.status == "SUCCEEDED",
        )
        .with_for_update()
    ).all()
    changes = []
    for usage in rows:
        if usage.prompt_tokens is None or usage.completion_tokens is None:
            continue
        old = micro_cny(usage.prompt_tokens, OLD_INPUT_RATE) + micro_cny(
            usage.completion_tokens, OLD_OUTPUT_RATE
        )
        if usage.charged_micro_cny != old:
            continue  # Already corrected, or charged with a different tariff.
        new = micro_cny(usage.prompt_tokens, NEW_INPUT_RATE) + micro_cny(
            usage.completion_tokens, NEW_OUTPUT_RATE
        )
        changes.append((usage, old, new))
    reduction = sum(old - new for _, old, new in changes)
    result = {
        "month": month,
        "calls": len(changes),
        "old_estimate_cny": sum(old for _, old, _ in changes) / 1_000_000,
        "new_estimate_cny": sum(new for _, _, new in changes) / 1_000_000,
        "budget_before_cny": budget.spent_micro_cny / 1_000_000,
        "budget_after_cny": (budget.spent_micro_cny - reduction) / 1_000_000,
        "unknown_and_other_calls_unchanged": True,
    }
    if apply and changes:
        for usage, _, new in changes:
            usage.charged_micro_cny = new
        budget.spent_micro_cny -= reduction
        db.add(
            AuditLog(
                action="reprice_cloud_usage",
                resource_type="cloud_budget_month",
                detail=json.dumps(
                    {**result, "old_rates": [100, 100], "new_rates": [2, 8]}, ensure_ascii=False
                ),
            )
        )
        db.commit()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct DeepSeek Flash app-side estimates")
    parser.add_argument("--month", default=datetime.now(UTC).strftime("%Y-%m"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with Session(get_engine()) as db:
        print(json.dumps(reprice_flash_month(db, args.month, args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
