"""Графики платежей: гибкая периодичность, частичная оплата, просрочка.

FR-SCH-1..5 + открытый вопрос §92 (просрочка/частичная оплата).

Семантика (ленивая, без фонового планировщика):
- `amount` — сумма за один период; `next_due_date` — крайний срок текущего
  (ещё не закрытого) периода; `paid_in_period` — сколько уже внесено в счёт
  этого периода (частичная оплата).
- Оплата накапливается в `paid_in_period`; как только набран полный `amount`,
  период закрывается и `next_due_date` сдвигается на период. Переплата
  переносится дальше и может закрыть сразу несколько периодов.
- Просрочка считается на момент запроса: сколько крайних сроков уже наступило
  и не закрыто. Долг = остаток текущего периода + полные суммы остальных
  наступивших периодов.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentSchedule, SchedulePeriod

_CENT = Decimal("0.01")
# Защита от зацикливания при подсчёте наступивших периодов (advance всегда
# двигает дату вперёд минимум на день, но предел оставляем на всякий случай).
_MAX_PERIODS = 100_000


# Деньги приходят из разных источников: float от ИИ, Decimal из БД, str из ввода.
Money = Decimal | float | int | str


def _money(value: Money) -> Decimal:
    """Приводит число к деньгам с 2 знаками."""
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _aware(dt: datetime) -> datetime:
    """Гарантирует tz-aware datetime (в БД/SQLite дата может быть naive)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def fmt_money(value: Money) -> str:
    """Единообразное отображение суммы (например, 1500.00)."""
    return f"{_money(value):.2f}"


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


def count_due_periods(
    next_due: datetime,
    now: datetime,
    period: SchedulePeriod,
    interval_days: int | None,
) -> int:
    """Сколько крайних сроков уже наступило (<= now), начиная с next_due.

    0 — срок ещё не наступил; 1 — наступил текущий период; >1 — накопилась
    просрочка на несколько периодов.
    """
    now = _aware(now)
    d = _aware(next_due)
    count = 0
    while d <= now and count < _MAX_PERIODS:
        count += 1
        d = advance_due_date(d, period, interval_days)
    return count


def period_label(period: SchedulePeriod, interval_days: int | None = None) -> str:
    labels = {
        SchedulePeriod.daily: "ежедневно",
        SchedulePeriod.weekly: "еженедельно",
        SchedulePeriod.monthly: "ежемесячно",
    }
    if period is SchedulePeriod.custom:
        return f"каждые {interval_days} дн."
    return labels.get(period, str(period))


@dataclass
class ScheduleStatus:
    """Состояние графика на момент `now` (расчётное, без мутаций)."""

    next_due_date: datetime
    amount: Decimal  # сумма за период
    paid_in_period: Decimal  # внесено в текущий период
    remaining_current: Decimal  # сколько ещё нужно, чтобы закрыть текущий период
    overdue_periods: int  # сколько сроков наступило и не закрыто (0 — не просрочен)
    overdue_days: int  # на сколько дней просрочен ближайший срок
    debt_now: Decimal  # сколько нужно внести сейчас, чтобы не быть в просрочке
    is_overdue: bool


@dataclass
class ApplyResult:
    """Итог применения платежа к графику."""

    periods_closed: int  # сколько полных периодов закрыл платёж
    paid_in_period: Decimal  # остаток-предоплата текущего периода после платежа
    remaining_current: Decimal  # сколько ещё до закрытия текущего периода
    next_due_date: datetime


def schedule_status(
    schedule: PaymentSchedule, now: datetime | None = None
) -> ScheduleStatus:
    """Ленивый расчёт просрочки/долга по графику на момент `now`."""
    if now is None:
        now = datetime.now(timezone.utc)
    amount = _money(schedule.amount)
    paid = _money(schedule.paid_in_period)
    # Переплата на неактивном графике не должна давать отрицательный остаток.
    remaining_current = max(amount - paid, Decimal("0.00"))
    next_due = _aware(schedule.next_due_date)

    overdue_periods = count_due_periods(
        next_due, now, schedule.period, schedule.interval_days
    )
    is_overdue = overdue_periods >= 1
    if is_overdue:
        debt_now = remaining_current + (overdue_periods - 1) * amount
        overdue_days = (now - next_due).days
    else:
        debt_now = Decimal("0.00")
        overdue_days = 0

    return ScheduleStatus(
        next_due_date=next_due,
        amount=amount,
        paid_in_period=paid,
        remaining_current=remaining_current,
        overdue_periods=overdue_periods,
        overdue_days=overdue_days,
        debt_now=_money(debt_now),
        is_overdue=is_overdue,
    )


def due_summary(st: ScheduleStatus) -> str:
    """Компактная сводка состояния графика для отчётов и снимка ИИ.

    Разводит «срок наступил сегодня» и «просрочен N дней» — это разные вещи.
    """
    if st.is_overdue:
        if st.overdue_days >= 1:
            return f"просрочен {st.overdue_days} дн., долг {fmt_money(st.debt_now)}"
        return f"срок сегодня, к оплате {fmt_money(st.debt_now)}"
    if st.remaining_current <= 0:
        return f"оплачен, след. платёж {st.next_due_date:%d.%m.%Y}"
    if st.remaining_current < st.amount:
        return (
            f"частично оплачен, остаток {fmt_money(st.remaining_current)} "
            f"к {st.next_due_date:%d.%m.%Y}"
        )
    return f"след. платёж {st.next_due_date:%d.%m.%Y}"


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
    """Создаёт или обновляет график водителя (у водителя один график).

    Новые условия обнуляют накопленную частичную оплату — отсчёт начинается
    заново от указанной суммы и даты первого платежа.
    """
    schedule = await get_schedule(session, driver_id)
    if schedule is None:
        schedule = PaymentSchedule(driver_id=driver_id)
        session.add(schedule)
    schedule.period = period
    schedule.interval_days = interval_days
    schedule.amount = amount
    schedule.paid_in_period = 0
    schedule.next_due_date = next_due_date
    schedule.active = True
    await session.commit()
    await session.refresh(schedule)
    return schedule


async def apply_payment(
    session: AsyncSession,
    schedule: PaymentSchedule,
    paid_amount: Money,
    *,
    commit: bool = True,
) -> ApplyResult:
    """Засчитывает оплату: копит частичную, закрывает полные периоды, сдвигает срок.

    Переплата закрывает несколько периодов подряд. Остаток (< amount) остаётся
    предоплатой следующего периода. Неактивный график только копит внесённое,
    срок не двигает.
    """
    amount = _money(schedule.amount)
    credit = _money(schedule.paid_in_period) + _money(paid_amount)
    periods = 0

    if schedule.active and amount > 0:
        while credit >= amount and periods < _MAX_PERIODS:
            credit -= amount
            schedule.next_due_date = advance_due_date(
                schedule.next_due_date, schedule.period, schedule.interval_days
            )
            periods += 1

    schedule.paid_in_period = credit
    # commit=False — платёж и график должны лечь одной транзакцией.
    if commit:
        await session.commit()
    else:
        await session.flush()
    return ApplyResult(
        periods_closed=periods,
        paid_in_period=credit,
        remaining_current=max(amount - credit, Decimal("0.00")),
        next_due_date=_aware(schedule.next_due_date),
    )
