"""Выборки для кнопочных отчётов владельца (FR-RPT-1..3)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Car, Driver, Payment, PaymentSchedule


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
    amount: float
    next_due: object  # datetime


async def cars_with_drivers(session: AsyncSession) -> list[Car]:
    """Все машины с подгруженным водителем (FR-RPT-1)."""
    result = await session.scalars(
        select(Car).options(selectinload(Car.driver)).order_by(Car.id)
    )
    return list(result.all())


async def upcoming_payments(session: AsyncSession) -> list[UpcomingItem]:
    """Водители с активным графиком, отсортированные по ближайшей дате (FR-RPT-2)."""
    rows = await session.execute(
        select(PaymentSchedule, Driver, Car)
        .join(Driver, PaymentSchedule.driver_id == Driver.id)
        .join(Car, Driver.car_id == Car.id, isouter=True)
        .where(PaymentSchedule.active.is_(True))
        .order_by(PaymentSchedule.next_due_date)
    )
    items: list[UpcomingItem] = []
    for schedule, driver, car in rows.all():
        items.append(
            UpcomingItem(
                name=driver.full_name,
                car_plate=car.plate if car else "—",
                amount=float(schedule.amount),
                next_due=schedule.next_due_date,
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

    lines += ["", "Графики платежей (ближайшие даты):"]
    if upcoming:
        for it in upcoming:
            lines.append(
                f"- {it.name} ({it.car_plate}): {it.amount}, "
                f"след. платёж {it.next_due:%d.%m.%Y}"
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
