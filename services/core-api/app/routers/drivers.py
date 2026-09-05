"""Роутер drivers: список, карточка, регистрация по инвайту, увольнение."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import Driver
from app.db.session import get_session
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import invitations as invitations_service
from app.errors import Conflict, NotFound
from contracts import DriverDTO, DriverRegister, DriverStatsDTO, DriverWithStats

router = APIRouter()


class FireResult(BaseModel):
    """Локальная модель ответа увольнения — такого DTO в contracts нет."""

    freed_plate: str | None = None


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _driver_dto(driver: Driver, car_plate: str | None = None) -> DriverDTO:
    return DriverDTO(
        id=driver.id,
        tg_user_id=driver.tg_user_id,
        full_name=driver.full_name,
        phone=driver.phone,
        inn=driver.inn,
        selfie_file_id=driver.selfie_file_id,
        selfie_path=driver.selfie_path,
        car_id=driver.car_id,
        car_plate=car_plate,
        active=driver.active,
        fired_at=_utc(driver.fired_at),
        created_at=_utc(driver.created_at),
    )


@router.get("", response_model=list[DriverDTO])
async def list_drivers(
    active: bool = True,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[DriverDTO]:
    drivers = await drivers_service.list_drivers(session, active=active)
    # list_drivers грузит Driver.car через selectinload — car доступен синхронно.
    return [_driver_dto(d, d.car.plate if d.car else None) for d in drivers]


@router.get("/{driver_id}", response_model=DriverWithStats)
async def get_driver(
    driver_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> DriverWithStats:
    driver = await drivers_service.get_driver(session, driver_id)
    if driver is None:
        raise NotFound(f"водитель {driver_id} не найден")
    stats = await drivers_service.driver_stats(session, driver_id)
    return DriverWithStats(
        driver=_driver_dto(driver, driver.car.plate if driver.car else None),
        stats=DriverStatsDTO(
            payments_count=stats.payments_count,
            total_paid=Decimal(str(stats.total_paid)),
        ),
    )


@router.post("/register", response_model=DriverDTO)
async def register(
    payload: DriverRegister,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> DriverDTO:
    check = await invitations_service.resolve_invitation(session, payload.code)
    if not check.ok:
        raise Conflict(check.problem.value)

    driver = await drivers_service.register_driver(
        session,
        tg_user_id=payload.tg_user_id,
        full_name=payload.full_name,
        phone=payload.phone,
        inn=payload.inn or "",
        selfie_file_id=payload.selfie_file_id,
        selfie_path=payload.selfie_path,
        car_id=check.invitation.car_id,
        commit=False,
    )
    await invitations_service.mark_used(
        session, check.invitation, used_by=payload.tg_user_id, commit=False
    )
    # Регистрация и гашение инвайта — одна транзакция: иначе бывает водитель
    # без погашенного инвайта или наоборот.
    await session.commit()
    await session.refresh(driver)

    car = await cars_service.get_car(session, driver.car_id)
    return _driver_dto(driver, car.plate if car else None)


@router.post("/{driver_id}/fire", response_model=FireResult)
async def fire(
    driver_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> FireResult:
    driver = await drivers_service.get_driver(session, driver_id)
    if driver is None:
        raise NotFound(f"водитель {driver_id} не найден")
    plate = await drivers_service.fire_driver(session, driver)
    return FireResult(freed_plate=plate)
