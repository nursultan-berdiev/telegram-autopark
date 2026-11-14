"""Обзор парка «одна машина — одна карточка» для главной админки.

Только чтение. Каждый блок данных — один запрос на весь парк, а не по запросу
на машину: карточек столько же, сколько машин, и N обращений с главной не нужно.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Alert,
    AlertStatus,
    Car,
    CarState,
    Command,
    Driver,
    Fine,
    FineStatus,
    Payment,
    PhotoInspection,
)
from app.domain import cars as cars_domain
from app.domain import commands as commands_domain
from app.domain import payments as payments_domain
from app.domain import photo_inspections as photo_domain
from app.domain import reports as reports_domain
from app.domain import telemetry as telemetry_domain


@dataclass
class CarCard:
    car: Car
    driver: Driver | None
    last_payment: Payment | None
    schedule: reports_domain.UpcomingItem | None  # график оплат текущего водителя
    state: CarState | None
    online: bool
    block: Command | None  # действующая команда блокировки
    last_inspection_at: datetime | None
    photo: photo_domain.PhotoStatus | None  # срок фотоконтроля текущего водителя
    unpaid_fines: int
    open_alerts: int


async def _count_by_car(session: AsyncSession, model, condition) -> dict[int, int]:
    rows = await session.execute(
        select(model.car_id, func.count()).where(condition).group_by(model.car_id)
    )
    return {car_id: count for car_id, count in rows.all()}


async def cards(session: AsyncSession, now: datetime) -> list[CarCard]:
    cars = await cars_domain.list_cars(session)
    drivers = {
        d.car_id: d
        for d in await session.scalars(
            select(Driver).where(Driver.car_id.is_not(None), Driver.active.is_(True))
        )
    }
    # График и фотоконтроль привязаны к водителю: берём их у того, кто за рулём сейчас.
    schedules = {i.driver_id: i for i in await reports_domain.upcoming_payments(session, now)}
    photo = {s.driver_id: s for s in await photo_domain.statuses(session, now)}
    last_payments = await payments_domain.last_by_car(session)
    states = await telemetry_domain.list_states(session)
    blocks = {c.car_id: c for c in await commands_domain.cars_under_block(session)}
    last_inspections = dict(
        (
            await session.execute(
                select(PhotoInspection.car_id, func.max(PhotoInspection.created_at))
                .where(PhotoInspection.car_id.is_not(None))
                .group_by(PhotoInspection.car_id)
            )
        ).all()
    )
    fines = await _count_by_car(session, Fine, Fine.status == FineStatus.unpaid)
    alerts = await _count_by_car(session, Alert, Alert.status == AlertStatus.open)

    result: list[CarCard] = []
    for car in cars:
        driver = drivers.get(car.id)
        state = states.get(car.id)
        result.append(
            CarCard(
                car=car,
                driver=driver,
                last_payment=last_payments.get(car.id),
                schedule=schedules.get(driver.id) if driver else None,
                state=state,
                online=telemetry_domain.is_online(state, now),
                block=blocks.get(car.id),
                last_inspection_at=last_inspections.get(car.id),
                photo=photo.get(driver.id) if driver else None,
                unpaid_fines=fines.get(car.id, 0),
                open_alerts=alerts.get(car.id, 0),
            )
        )
    return result
