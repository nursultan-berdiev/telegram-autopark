"""Ежедневные напоминания о платежах.

Водителю — накануне, в день срока и при просрочке (не чаще раза в день), кроме
воскресенья: выходной. Владельцу — одна утренняя сводка: кто платит сегодня,
кто должен; её шлём и в воскресенье.

Отбор вынесен в чистую функцию `collect`, отправка — отдельно: так логику можно
проверить тестами без Telegram.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from html import escape as _esc
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Car, Driver, PaymentSchedule
from app.domain import commands as commands_domain
from app.domain import payments as payments_domain
from app.domain import reports as reports_domain
from app.domain.schedules import (
    DAY_OFF,
    ScheduleStatus,
    due_summary,
    fmt_money,
    schedule_status,
)

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
    now = _normalize_now(now)
    tzinfo = ZoneInfo(tz)
    today = now.astimezone(tzinfo).date()
    day_off = today.weekday() == DAY_OFF

    plan = Plan()
    for schedule, driver, car in await _active_schedules(session):
        st = schedule_status(schedule, now)
        plate = car.plate
        due_local = st.next_due_date.astimezone(tzinfo).date()

        kind = _kind_for(st, due_local, today)
        if kind is None:
            continue

        # Сводка владельцу — полная картина за день, независимо от анти-спама.
        # Имя/номер экранируем: бот шлёт с parse_mode=HTML (plan/06), а '<'/'&'
        # в имени водителя иначе обрушат всё сообщение.
        plan.owner_lines.append(
            f"{_esc(driver.full_name)} · {_esc(plate)}: {due_summary(st)}"
        )
        if st.is_overdue:
            plan.total_debt += float(st.debt_now)

        if day_off:
            continue
        # Водителю — не чаще одного напоминания в локальный день.
        if not force and schedule.last_reminded_on == today:
            continue

        plan.reminders.append(
            DriverReminder(
                schedule_id=schedule.id,
                tg_user_id=driver.tg_user_id,
                kind=kind,
                text=_driver_text(kind, st, _esc(plate)),
            )
        )

    return plan


async def mark_reminded(
    session: AsyncSession, schedule_ids: list[int], today: date
) -> None:
    """Помечает графики как «напомнили сегодня»."""
    await _mark(session, schedule_ids, today, "last_reminded_on")


# --- Вечерние уведомления у срока 22:00 (три независимо включаемые задачи) ----
#
# Считаем на стороне core-api, доставляет бот (direct send, см. plan/03). Каждая
# задача — свой антиспам-столбец (last_warned_on / last_block_notice_on), поэтому
# перезапуск бота около срока не повторит рассылку.

# Источник команды → как назвать блок в сводке владельцу.
_BLOCK_SOURCE_LABEL = {"overdue": "за аренду", "fines": "за штрафы", "manual": "вручную"}


@dataclass
class DriverNoticeRow:
    schedule_id: int
    tg_user_id: int
    text: str


def _warn_text(plate: str, due_hm: str) -> str:
    # Время блокировки берём из самого графика (срок next_due_date), а не из
    # отдельной константы — иначе текст разъедется с реальным сроком. plate
    # экранируем: бот шлёт с parse_mode=HTML.
    return (
        f"⏰ Напоминание: аренда за автомобиль {_esc(plate)} сегодня не оплачена. "
        f"В {due_hm} двигатель будет заблокирован за неоплату. "
        "Пожалуйста, внесите оплату, чтобы избежать блокировки."
    )


def _block_notice_text(plate: str) -> str:
    return (
        f"🔴 Аренда за автомобиль {_esc(plate)} не оплачена в срок. Блокировка "
        "двигателя включена. Внесите оплату — блокировка снимется автоматически."
    )


def _normalize_now(now: datetime) -> datetime:
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


async def _active_schedules(session: AsyncSession):
    """Активные графики активных водителей С МАШИНОЙ — ровно те кандидаты, по
    которым работает блокировка (`engine_enforcement.overdue_car_ids`). Один
    источник для утренних напоминаний и вечерних уведомлений у срока."""
    rows = await session.execute(
        select(PaymentSchedule, Driver, Car)
        .join(Driver, PaymentSchedule.driver_id == Driver.id)
        .join(Car, Driver.car_id == Car.id)
        .where(
            PaymentSchedule.active.is_(True),
            Driver.active.is_(True),
            Driver.car_id.is_not(None),
        )
        .order_by(PaymentSchedule.next_due_date)
    )
    return rows.all()


async def collect_overdue_warning(
    session: AsyncSession,
    now: datetime,
    tz: str,
    *,
    lead_minutes: int,
    force: bool = False,
) -> list[DriverNoticeRow]:
    """Водители, у кого срок не оплачен и наступит в ближайшие `lead_minutes`.

    Только ещё НЕ просроченные с непогашенным остатком — это предупреждение до
    блокировки, а не уведомление о ней. Антиспам: `last_warned_on` за локальный
    день (force его игнорирует — для ручной перепроверки)."""
    now = _normalize_now(now)
    tzinfo = ZoneInfo(tz)
    today = now.astimezone(tzinfo).date()
    lead = timedelta(minutes=max(1, lead_minutes))
    out: list[DriverNoticeRow] = []
    for schedule, driver, car in await _active_schedules(session):
        if not force and schedule.last_warned_on == today:
            continue
        st = schedule_status(schedule, now)
        if st.is_overdue or st.remaining_current <= 0:
            continue
        if not (timedelta(0) < st.next_due_date - now <= lead):
            continue
        due_hm = st.next_due_date.astimezone(tzinfo).strftime("%H:%M")
        out.append(
            DriverNoticeRow(schedule.id, driver.tg_user_id, _warn_text(car.plate, due_hm))
        )
    return out


async def collect_block_notice(
    session: AsyncSession, now: datetime, tz: str, *, force: bool = False
) -> list[DriverNoticeRow]:
    """Водители, просроченные к сроку (та же выборка, что блокирует задача в 22:00).

    По согласованию с заказчиком уведомляем по признаку просрочки (`is_overdue`),
    НЕ по фактическому состоянию реле: в 22:00 блок только взводится и сработает
    на ближайшей остановке, а формулировку «блокировка включена» заказчик выбрал
    явно. Фактические сработавшие блоки (с учётом реле/правил) отдельно видны в
    админ-дайджесте через `cars_under_block`; эти две картины могут кратко
    расходиться — это ожидаемо. Антиспам: `last_block_notice_on` за локальный день."""
    now = _normalize_now(now)
    today = now.astimezone(ZoneInfo(tz)).date()
    out: list[DriverNoticeRow] = []
    for schedule, driver, car in await _active_schedules(session):
        if not force and schedule.last_block_notice_on == today:
            continue
        if not schedule_status(schedule, now).is_overdue:
            continue
        out.append(
            DriverNoticeRow(schedule.id, driver.tg_user_id, _block_notice_text(car.plate))
        )
    return out


async def collect_admin_digest(
    session: AsyncSession, now: datetime, tz: str
) -> str:
    """Готовая сводка владельцу: кто оплатил сегодня, кто просрочил, автоблокировки."""
    now = _normalize_now(now)
    now_local = now.astimezone(ZoneInfo(tz))

    # Один и тот же `now` во все расчёты, иначе «оплатили» и «просрочено» будут
    # про разные моменты (и тесты поплывут у полуночи).
    paid = await payments_domain.paid_today(session, tz, now=now)
    upcoming = await reports_domain.upcoming_payments(session, now)
    overdue = [it for it in upcoming if it.is_overdue]

    cars = await reports_domain.cars_with_drivers(session)
    name_by_car = {c.id: (c.driver.full_name if c.driver else "—") for c in cars}
    plate_by_car = {c.id: c.plate for c in cars}
    blocks = await commands_domain.cars_under_block(session)

    # Имена/номера — пользовательский ввод, экранируем под parse_mode=HTML.
    lines = [f"📊 <b>Платежи за {now_local:%d.%m}, {now_local:%H:%M}</b>", ""]

    if paid:
        lines.append(f"✅ Оплатили сегодня ({len(paid)}):")
        lines += [
            f"• {_esc(plate or '—')} — {_esc(name or '—')} — {fmt_money(amount)} сом"
            for _, name, plate, amount in paid
        ]
    else:
        lines.append("✅ Оплатили сегодня: —")
    lines.append("")

    if overdue:
        lines.append(f"❌ Не оплатили / просрочено ({len(overdue)}):")
        lines += [
            f"• {_esc(it.car_plate)} — {_esc(it.name)}: {it.summary}" for it in overdue
        ]
    else:
        lines.append("❌ Не оплатили: нет, все оплатили 🎉")

    if blocks:
        lines += ["", f"🔒 Автоблокировка ({len(blocks)}):"]
        for cmd in blocks:
            label = _BLOCK_SOURCE_LABEL.get(cmd.source, cmd.source or "—")
            plate = plate_by_car.get(cmd.car_id, f"#{cmd.car_id}")
            lines.append(
                f"• {_esc(plate)} — {_esc(name_by_car.get(cmd.car_id, '—'))} — {label}"
            )
    else:
        lines += ["", "🔒 Автоблокировок нет."]

    return "\n".join(lines)


async def _mark(
    session: AsyncSession, schedule_ids: list[int], today: date, column: str
) -> None:
    if not schedule_ids:
        return
    result = await session.scalars(
        select(PaymentSchedule).where(PaymentSchedule.id.in_(schedule_ids))
    )
    for schedule in result.all():
        setattr(schedule, column, today)
    await session.commit()


async def mark_warned(
    session: AsyncSession, schedule_ids: list[int], today: date
) -> None:
    await _mark(session, schedule_ids, today, "last_warned_on")


async def mark_block_notice(
    session: AsyncSession, schedule_ids: list[int], today: date
) -> None:
    await _mark(session, schedule_ids, today, "last_block_notice_on")
