"""Графики платежей: гибкая периодичность (FR-SCH-1..5)."""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import PaymentSchedule, SchedulePeriod


def add_months(dt: datetime, months: int) -> datetime:
    """Прибавляет календарные месяцы с корректировкой конца месяца."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def advance_due_date(
    current: datetime, period: SchedulePeriod, interval_days: int | None
) -> datetime:
    """Вычисляет следующую дату платежа от текущей по периодичности."""
    if period is SchedulePeriod.daily:
        return current + timedelta(days=1)
    if period is SchedulePeriod.weekly:
        return current + timedelta(days=7)
    if period is SchedulePeriod.monthly:
        return add_months(current, 1)
    if period is SchedulePeriod.custom:
        return current + timedelta(days=interval_days or 1)
    raise ValueError(f"Неизвестная периодичность: {period}")


def period_label(period: SchedulePeriod, interval_days: int | None = None) -> str:
    labels = {
        SchedulePeriod.daily: "ежедневно",
        SchedulePeriod.weekly: "еженедельно",
        SchedulePeriod.monthly: "ежемесячно",
    }
    if period is SchedulePeriod.custom:
        return f"каждые {interval_days} дн."
    return labels.get(period, str(period))


async def get_schedule(
    session: AsyncSession, driver_id: int
) -> PaymentSchedule | None:
    return await session.scalar(
        select(PaymentSchedule).where(PaymentSchedule.driver_id == driver_id)
    )


async def set_schedule(
    session: AsyncSession,
    *,
    driver_id: int,
    period: SchedulePeriod,
    interval_days: int | None,
    amount: float,
    next_due_date: datetime,
) -> PaymentSchedule:
    """Создаёт или обновляет график водителя (у водителя один график)."""
    schedule = await get_schedule(session, driver_id)
    if schedule is None:
        schedule = PaymentSchedule(driver_id=driver_id)
        session.add(schedule)
    schedule.period = period
    schedule.interval_days = interval_days
    schedule.amount = amount
    schedule.next_due_date = next_due_date
    schedule.active = True
    await session.commit()
    await session.refresh(schedule)
    return schedule


async def bump_after_payment(
    session: AsyncSession, schedule: PaymentSchedule
) -> None:
    """Сдвигает next_due_date на один период (после подтверждённой оплаты)."""
    schedule.next_due_date = advance_due_date(
        schedule.next_due_date, schedule.period, schedule.interval_days
    )
    await session.commit()
