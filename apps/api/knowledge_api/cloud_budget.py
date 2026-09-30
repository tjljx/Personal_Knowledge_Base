"""Atomic monthly reservations for paid cloud model calls."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from knowledge_api.config import Settings
from knowledge_api.model_gateway import ModelAnswer, Source
from knowledge_api.models import CloudBudgetMonth, CloudModelUsage


class BudgetNotConfigured(Exception):
    pass


class BudgetExceeded(Exception):
    pass


def micro_cny(tokens: int, rate_per_million: Decimal) -> int:
    # A price in CNY per million tokens is numerically micro-CNY per token.
    return int((Decimal(tokens) * rate_per_million).to_integral_value(rounding=ROUND_CEILING))


def budget_limit(settings: Settings) -> int:
    return int((settings.cloud_monthly_budget_cny * 1_000_000).to_integral_value())


def reserve_call(
    db: Session,
    settings: Settings,
    user_id: str,
    kb_id: str,
    question: str,
    sources: list[Source],
    history: list[dict[str, str]] | None,
    task: str = "knowledge_qa",
    max_output_tokens: int | None = None,
) -> CloudModelUsage:
    if settings.model_api_key is None:
        raise BudgetNotConfigured("未配置模型 API 密钥")
    if (
        settings.model_input_cny_per_million is None
        or settings.model_output_cny_per_million is None
    ):
        raise BudgetNotConfigured("未配置云模型输入和输出费用上界")
    input_bytes = sum(
        len(text.encode("utf-8"))
        for text in [
            question,
            *(source.content for source in sources),
            *(message.get("content", "")[:2000] for message in (history or [])[-8:]),
        ]
    )
    reserve = micro_cny(input_bytes + 4096, settings.model_input_cny_per_million)
    reserve += micro_cny(
        max_output_tokens or settings.model_max_output_tokens, settings.model_output_cny_per_million
    )
    month = datetime.now(UTC).strftime("%Y-%m")
    if db.get(CloudBudgetMonth, month) is None:
        try:
            with db.begin_nested():
                db.add(CloudBudgetMonth(month=month, spent_micro_cny=0, reserved_micro_cny=0))
                db.flush()
        except IntegrityError:
            db.expire_all()
    result = db.execute(
        update(CloudBudgetMonth)
        .where(
            CloudBudgetMonth.month == month,
            CloudBudgetMonth.spent_micro_cny + CloudBudgetMonth.reserved_micro_cny + reserve
            <= budget_limit(settings),
        )
        .values(reserved_micro_cny=CloudBudgetMonth.reserved_micro_cny + reserve)
    )
    if result.rowcount != 1:
        db.rollback()
        raise BudgetExceeded("本月云模型预算不足，已暂停云端问答")
    usage = CloudModelUsage(
        month=month,
        user_id=user_id,
        knowledge_base_id=kb_id,
        task=task,
        provider=settings.model_provider,
        model=settings.model_name,
        status="PENDING",
        reserved_micro_cny=reserve,
        charged_micro_cny=0,
    )
    db.add(usage)
    db.commit()  # Reservation is durable before the paid request starts.
    db.refresh(usage)
    return usage


def settle_call(
    db: Session, settings: Settings, usage: CloudModelUsage, answer: ModelAnswer | None
) -> None:
    if (
        answer is not None
        and answer.prompt_tokens is not None
        and answer.completion_tokens is not None
    ):
        charged = micro_cny(answer.prompt_tokens, settings.model_input_cny_per_million)
        charged += micro_cny(answer.completion_tokens, settings.model_output_cny_per_million)
        usage.prompt_tokens = answer.prompt_tokens
        usage.completion_tokens = answer.completion_tokens
        usage.status = "SUCCEEDED"
    else:
        # A failed or usage-less response might still be billed by the provider.
        charged = usage.reserved_micro_cny
        usage.status = "UNKNOWN"
    usage.charged_micro_cny = charged
    db.execute(
        update(CloudBudgetMonth)
        .where(CloudBudgetMonth.month == usage.month)
        .values(
            reserved_micro_cny=CloudBudgetMonth.reserved_micro_cny - usage.reserved_micro_cny,
            spent_micro_cny=CloudBudgetMonth.spent_micro_cny + charged,
        )
    )
    db.commit()


def reconcile_stale_calls(db: Session, settings: Settings) -> bool:
    """Charge abandoned reservations conservatively after model calls can no longer run."""
    stale = db.scalar(
        select(CloudModelUsage)
        .where(
            CloudModelUsage.status == "PENDING",
            CloudModelUsage.created_at < datetime.now(UTC) - timedelta(minutes=15),
        )
        .order_by(CloudModelUsage.created_at, CloudModelUsage.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if stale is None:
        return False
    settle_call(db, settings, stale, None)
    return True


def monthly_summary(db: Session, settings: Settings) -> dict:
    month = datetime.now(UTC).strftime("%Y-%m")
    row = db.get(CloudBudgetMonth, month)
    spent = row.spent_micro_cny if row else 0
    reserved = row.reserved_micro_cny if row else 0
    limit = budget_limit(settings)
    percent = (spent + reserved) * 100 / limit
    return {
        "month": month,
        "budget_cny": limit / 1_000_000,
        "charged_cny": spent / 1_000_000,
        "reserved_cny": reserved / 1_000_000,
        "remaining_cny": max(0, limit - spent - reserved) / 1_000_000,
        "alert_level": 100
        if percent >= 100
        else 90
        if percent >= 90
        else 70
        if percent >= 70
        else 0,
    }
