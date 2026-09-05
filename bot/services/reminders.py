"""Ежедневные напоминания о платежах.

Водителю — накануне, в день срока и при просрочке (не чаще раза в день).
Владельцу — одна утренняя сводка: кто платит сегодня, кто должен.

Отбор вынесен в чистую функцию `collect`, отправка — отдельно: так логику можно
проверить тестами без Telegram.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Car, Driver, PaymentSchedule
from bot.services.schedules import ScheduleStatus, due_summary, fmt_money, schedule_status

log = logging.getLogger(__name__)

# Виды напоминаний водителю.
KIND_OVERDUE = "overdue"
KIND_DUE_TODAY = "due_today"
KIND_TOMORROW = "tomorrow"


@dataclass
class DriverReminder:
    schedule_id: int
    tg_user_id: int
    kind: str
    text: str


@dataclass
class Plan:
    """Что рассылаем за один прогон."""

    reminders: list[DriverReminder] = field(default_factory=list)
    owner_lines: list[str] = field(default_factory=list)
    total_debt: float = 0.0

    def owner_digest(self) -> str | None:
        """Сводка владельцу. None — если напоминать не о чем."""
        if not self.owner_lines:
            return None
        lines = ["⏰ <b>Платежи на сегодня</b>\n", *self.owner_lines]
        if self.total_debt > 0:
            lines.append(f"\nСуммарный долг: {fmt_money(self.total_debt)}")
        return "\n".join(lines)


def _driver_text(kind: str, st: ScheduleStatus, car_plate: str) -> str:
    if kind == KIND_OVERDUE:
        return (
            f"⚠️ Просрочка {st.overdue_days} дн. по машине {car_plate}.\n"
            f"К оплате: {fmt_money(st.debt_now)}.\n"
            "Отправьте чек кнопкой «Оплатить»."
        )
    if kind == KIND_DUE_TODAY:
        return (
            f"📅 Сегодня срок платежа по машине {car_plate}.\n"
            f"К оплате: {fmt_money(st.debt_now)}.\n"
            "Отправьте чек кнопкой «Оплатить»."
        )
    return (
        f"🔔 Напоминание: завтра платёж по машине {car_plate}.\n"
        f"Сумма: {fmt_money(st.remaining_current)}."
    )


def _kind_for(st: ScheduleStatus, due_local: date, today: date) -> str | None:
    """Что напоминать: просрочка / срок сегодня / завтра / ничего."""
    if st.is_overdue:
        # overdue_days == 0 — срок наступил сегодня, это ещё не просрочка.
        return KIND_DUE_TODAY if st.overdue_days < 1 else KIND_OVERDUE
    if due_local == today + timedelta(days=1):
        return KIND_TOMORROW
    return None


async def collect(
    session: AsyncSession, now: datetime, tz: str, *, force: bool = False
) -> Plan:
    """Собирает план рассылки на момент `now` (локальный день — по tz).

    force=True игнорирует анти-спам (нужно для ручной перепроверки: иначе
    повторный прогон за тот же день не отправит ничего).
    """
    tzinfo = ZoneInfo(tz)
    today = now.astimezone(tzinfo).date()

    rows = await session.execute(
        select(PaymentSchedule, Driver, Car)
        .join(Driver, PaymentSchedule.driver_id == Driver.id)
        .join(Car, Driver.car_id == Car.id, isouter=True)
        .where(PaymentSchedule.active.is_(True))
        .order_by(PaymentSchedule.next_due_date)
    )

    plan = Plan()
    for schedule, driver, car in rows.all():
        st = schedule_status(schedule, now)
        plate = car.plate if car else "—"
        due_local = st.next_due_date.astimezone(tzinfo).date()

        kind = _kind_for(st, due_local, today)
        if kind is None:
            continue

        # Сводка владельцу — полная картина за день, независимо от анти-спама.
        plan.owner_lines.append(f"{driver.full_name} · {plate}: {due_summary(st)}")
        if st.is_overdue:
            plan.total_debt += float(st.debt_now)

        # Водителю — не чаще одного напоминания в локальный день.
        if not force and schedule.last_reminded_on == today:
            continue

        plan.reminders.append(
            DriverReminder(
                schedule_id=schedule.id,
                tg_user_id=driver.tg_user_id,
                kind=kind,
                text=_driver_text(kind, st, plate),
            )
        )

    return plan


async def mark_reminded(
    session: AsyncSession, schedule_ids: list[int], today: date
) -> None:
    """Помечает графики как «напомнили сегодня»."""
    if not schedule_ids:
        return
    result = await session.scalars(
        select(PaymentSchedule).where(PaymentSchedule.id.in_(schedule_ids))
    )
    for schedule in result.all():
        schedule.last_reminded_on = today
    await session.commit()
