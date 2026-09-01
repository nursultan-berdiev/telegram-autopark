"""Выборки для кнопочных отчётов владельца (FR-RPT-1..3)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Car, Driver, Payment, PaymentSchedule
from bot.services.schedules import due_summary, fmt_money, schedule_status


@dataclass
class DriverTotal:
    name: str
    car_plate: str
    total: float
    count: int


@dataclass
class CarTotal:
    plate: str
    total: float
    count: int


@dataclass
class UpcomingItem:
    name: str
    car_plate: str
    amount: float  # сумма за период
    next_due: datetime  # крайний срок текущего периода
    remaining_current: float = 0.0  # сколько ещё до закрытия текущего периода
    debt_now: float = 0.0  # долг на момент запроса (0, если не просрочен)
    overdue_periods: int = 0
    overdue_days: int = 0
    is_overdue: bool = False
    summary: str = ""  # готовая человекочитаемая сводка состояния


async def cars_with_drivers(session: AsyncSession) -> list[Car]:
    """Все машины с подгруженным водителем (FR-RPT-1)."""
    result = await session.scalars(
        select(Car).options(selectinload(Car.driver)).order_by(Car.id)
    )
    return list(result.all())


async def upcoming_payments(
    session: AsyncSession, now: datetime | None = None
) -> list[UpcomingItem]:
    """Водители с активным графиком, отсортированные по ближайшей дате (FR-RPT-2).

    Самые просроченные (с самой ранней датой) идут первыми. По каждому водителю
    считается статус: просрочка (дни/периоды), долг и остаток текущего периода.
    """
    rows = await session.execute(
        select(PaymentSchedule, Driver, Car)
        .join(Driver, PaymentSchedule.driver_id == Driver.id)
        .join(Car, Driver.car_id == Car.id, isouter=True)
        .where(PaymentSchedule.active.is_(True))
        .order_by(PaymentSchedule.next_due_date)
    )
    items: list[UpcomingItem] = []
    for schedule, driver, car in rows.all():
        st = schedule_status(schedule, now)
        items.append(
            UpcomingItem(
                name=driver.full_name,
                car_plate=car.plate if car else "—",
                amount=float(st.amount),
                next_due=st.next_due_date,
                remaining_current=float(st.remaining_current),
                debt_now=float(st.debt_now),
                overdue_periods=st.overdue_periods,
                overdue_days=st.overdue_days,
                is_overdue=st.is_overdue,
                summary=due_summary(st),
            )
        )
    return items


async def statement_by_driver(session: AsyncSession) -> list[DriverTotal]:
    """Выписка по оплатам в разрезе водителей (FR-RPT-3)."""
    rows = await session.execute(
        select(
            Driver,
            Car,
            func.coalesce(func.sum(Payment.amount), 0),
            func.count(Payment.id),
        )
        .join(Car, Driver.car_id == Car.id, isouter=True)
        .join(Payment, Payment.driver_id == Driver.id, isouter=True)
        .group_by(Driver.id, Car.id)
        .order_by(Driver.full_name)
    )
    return [
        DriverTotal(
            name=driver.full_name,
            car_plate=car.plate if car else "—",
            total=float(total),
            count=int(count),
        )
        for driver, car, total, count in rows.all()
    ]


async def build_snapshot(session: AsyncSession) -> str:
    """Компактный текстовый снимок состояния автопарка для ИИ-ассистента (FR-AI-6/7)."""
    cars = await cars_with_drivers(session)
    upcoming = await upcoming_payments(session)
    by_driver = await statement_by_driver(session)

    free = sum(1 for c in cars if c.driver is None)
    occupied = len(cars) - free

    lines: list[str] = [
        f"Всего машин: {len(cars)} (занято: {occupied}, свободно: {free}).",
        "",
        "Машины:",
    ]
    for c in cars:
        who = c.driver.full_name if c.driver else "свободна"
        lines.append(f"- {c.plate}: {who}")

    overdue_count = sum(1 for it in upcoming if it.is_overdue)
    total_debt = sum(it.debt_now for it in upcoming)
    lines += [
        "",
        f"Графики платежей (в просрочке: {overdue_count}, "
        f"суммарный долг: {fmt_money(total_debt)}):",
    ]
    if upcoming:
        for it in upcoming:
            lines.append(
                f"- {it.name} ({it.car_plate}): период {fmt_money(it.amount)}, "
                f"{it.summary}"
            )
    else:
        lines.append("- нет назначенных графиков")

    lines += ["", "Суммы оплат по водителям:"]
    for r in by_driver:
        lines.append(f"- {r.name} ({r.car_plate}): всего {r.total} за {r.count} платеж.")

    return "\n".join(lines)


async def statement_by_car(session: AsyncSession) -> list[CarTotal]:
    """Выписка по оплатам в разрезе машин (FR-RPT-3)."""
    rows = await session.execute(
        select(
            Car,
            func.coalesce(func.sum(Payment.amount), 0),
            func.count(Payment.id),
        )
        .join(Payment, Payment.car_id == Car.id, isouter=True)
        .group_by(Car.id)
        .order_by(Car.plate)
    )
    return [
        CarTotal(plate=car.plate, total=float(total), count=int(count))
        for car, total, count in rows.all()
    ]
