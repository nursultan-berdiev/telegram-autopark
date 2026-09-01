"""Роутер cars: справочник автопарка (см. plan/03)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import (
    Car,
    CarStatus,
    Command,
    Driver,
    Fine,
    MaintenanceItem,
    Telemetry,
)
from app.db.session import get_session
from app.domain import cars as cars_service
from app.errors import Conflict, NotFound
from contracts import CarCreate, CarDTO

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _drivers_by_car(session: AsyncSession, car_ids: list[int]) -> dict[int, Driver]:
    # domain.cars не грузит Car.driver — достаём активных водителей одним запросом.
    if not car_ids:
        return {}
    rows = await session.scalars(
        select(Driver).where(Driver.car_id.in_(car_ids), Driver.active.is_(True))
    )
    return {d.car_id: d for d in rows}


def _car_dto(car: Car, driver: Driver | None) -> CarDTO:
    return CarDTO(
        id=car.id,
        plate=car.plate,
        model=car.model,
        status=car.status.value,
        photo_file_id=car.photo_file_id,
        photo_path=car.photo_path,
        created_at=_utc(car.created_at),
        driver_id=driver.id if driver else None,
        driver_name=driver.full_name if driver else None,
    )


@router.get("", response_model=list[CarDTO])
async def list_cars(
    free: bool = False,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[CarDTO]:
    cars = (
        await cars_service.list_free_cars(session)
        if free
        else await cars_service.list_cars(session)
    )
    drivers = await _drivers_by_car(session, [c.id for c in cars])
    return [_car_dto(c, drivers.get(c.id)) for c in cars]


@router.post("", response_model=CarDTO, status_code=201)
async def create_car(
    payload: CarCreate,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> CarDTO:
    plate = payload.plate.strip().upper()  # нормализация номера — как в боте
    if await cars_service.plate_exists(session, plate):
        raise Conflict(f"номер {plate} уже занят")
    car = await cars_service.create_car(
        session,
        plate=plate,
        model=payload.model,
        photo_file_id=payload.photo_file_id,
        photo_path=payload.photo_path,
    )
    return _car_dto(car, None)


@router.get("/{car_id}", response_model=CarDTO)
async def get_car(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> CarDTO:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    driver = await session.scalar(
        select(Driver).where(Driver.car_id == car.id, Driver.active.is_(True))
    )
    return _car_dto(car, driver)


async def _history_counters(session: AsyncSession, car_id: int) -> dict[str, int]:
    """Что именно потеряется вместе с машиной."""
    counters: dict[str, int] = {}
    for label, model in (
        ("телеметрия", Telemetry),
        ("штрафы", Fine),
        ("ТО", MaintenanceItem),
        ("команды", Command),
    ):
        count = await session.scalar(
            select(func.count()).select_from(model).where(model.car_id == car_id)
        )
        if count:
            counters[label] = int(count)
    return counters


@router.delete("/{car_id}", status_code=204, response_model=None)
async def delete_car(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> None:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    if car.status is not CarStatus.free:
        raise Conflict(f"машина {car.plate} занята водителем")

    # На PostgreSQL FK стоят на CASCADE: удаление молча снесло бы телеметрию,
    # штрафы, ТО и аудит команд. Историю удаляем только осознанно, не мимоходом.
    history = await _history_counters(session, car_id)
    if history:
        parts = ", ".join(f"{name}: {count}" for name, count in history.items())
        raise Conflict(
            f"по машине {car.plate} есть история ({parts}) — удаление уничтожит её"
        )

    await cars_service.delete_car(session, car)
