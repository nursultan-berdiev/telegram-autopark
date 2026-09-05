"""Эвалуаторы правил v1: чистые функции над состоянием БД."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Car, CarState, Driver, MaintenanceItem, PaymentSchedule
from app.domain import fines as fines_domain
from app.domain import schedules as schedules_domain


@dataclass
class RuleHit:
    triggered: bool
    payload: dict
    human: str


async def overdue_payment(
    session: AsyncSession, car: Car, params: dict, now: datetime | None = None
) -> RuleHit:
    """Просрочка считается доменной логикой графика — не дублируем расчёт."""
    now = now or datetime.now(timezone.utc)
    min_days = int(params.get("min_days", 1))
    min_debt = Decimal(str(params.get("min_debt", 0)))

    driver = await session.scalar(
        select(Driver).where(Driver.car_id == car.id, Driver.active.is_(True))
    )
    if driver is None:
        return RuleHit(False, {}, "")

    schedule = await session.scalar(
        select(PaymentSchedule).where(
            PaymentSchedule.driver_id == driver.id, PaymentSchedule.active.is_(True)
        )
    )
    if schedule is None:
        return RuleHit(False, {}, "")

    status = schedules_domain.schedule_status(schedule, now)
    triggered = (
        status.is_overdue
        and status.overdue_days >= min_days
        and status.debt_now >= min_debt
    )
    payload = {
        "debt": str(status.debt_now),
        "overdue_days": status.overdue_days,
        "next_due_date": status.next_due_date.isoformat(),
        "driver_id": driver.id,
        "driver_name": driver.full_name,
    }
    human = (
        f"просрочка платежа {status.overdue_days} дн., долг "
        f"{schedules_domain.fmt_money(status.debt_now)}"
    )
    return RuleHit(triggered, payload, human)


async def fines_count(
    session: AsyncSession, car: Car, params: dict, now: datetime | None = None
) -> RuleHit:
    """Считаем только неоплаченные — иначе алерт не закроется никогда."""
    threshold = int(params.get("count", 3))
    window_days = params.get("window_days")
    window_days = int(window_days) if window_days else None

    count = await fines_domain.count_unpaid(session, car.id, window_days=window_days)
    payload = {"unpaid_count": count, "window_days": window_days, "threshold": threshold}
    human = f"неоплаченных штрафов: {count} (порог {threshold})"
    return RuleHit(count >= threshold, payload, human)


async def maintenance_km(
    session: AsyncSession, car: Car, params: dict, now: datetime | None = None
) -> RuleHit:
    """Пробег берётся с трекера, поэтому база должна быть от того же устройства."""
    grace = Decimal(str(params.get("grace_km", 0)))
    state = await session.get(CarState, car.id)
    if state is None or state.odometer_km is None:
        return RuleHit(False, {}, "")

    items = list(
        await session.scalars(
            select(MaintenanceItem).where(MaintenanceItem.car_id == car.id)
        )
    )
    if not items:
        return RuleHit(False, {}, "")

    if not state.odometer_trusted:
        return RuleHit(False, {}, "")

    odometer = Decimal(str(state.odometer_km))
    overdue: list[tuple[Decimal, MaintenanceItem]] = []
    for item in items:
        # База снята с другого устройства — считать по ней нельзя.
        if item.last_service_tracker_id != state.odometer_tracker_id:
            continue
        over = odometer - Decimal(str(item.last_service_km)) - Decimal(str(item.interval_km))
        if over >= grace:
            overdue.append((over, item))

    if not overdue:
        return RuleHit(False, {}, "")

    # Порядок выборки из БД не определён — берём самое просроченное.
    over, item = max(overdue, key=lambda pair: pair[0])
    payload = {
        "type": item.type,
        "over_km": str(over),
        "odometer_km": str(odometer),
        "interval_km": str(item.interval_km),
        "odometer_tracker_id": state.odometer_tracker_id,
        "overdue_items": len(overdue),
    }
    return RuleHit(True, payload, f"пора ТО «{item.type}»: перепробег {over} км")


EVALUATORS = {
    "overdue_payment": overdue_payment,
    "fines_count": fines_count,
    "maintenance_km": maintenance_km,
}
