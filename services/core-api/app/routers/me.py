"""Роутер /me: определение роли (admin/driver/guest) по tg id (см. plan/03)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.config import settings
from app.db.models import Driver
from app.db.session import get_session
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from contracts import DriverDTO, MeDTO
from contracts.common import Role

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _driver_dto(session: AsyncSession, driver: Driver) -> DriverDTO:
    # get_driver_by_tg не грузит Driver.car — достаём машину отдельным запросом.
    car = await cars_service.get_car(session, driver.car_id) if driver.car_id else None
    return DriverDTO(
        id=driver.id,
        tg_user_id=driver.tg_user_id,
        full_name=driver.full_name,
        phone=driver.phone,
        inn=driver.inn,
        selfie_file_id=driver.selfie_file_id,
        selfie_path=driver.selfie_path,
        car_id=driver.car_id,
        car_plate=car.plate if car else None,
        active=driver.active,
        fired_at=_utc(driver.fired_at),
        created_at=_utc(driver.created_at),
    )


@router.get("/me", response_model=MeDTO)
async def me(
    tg_id: int | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> MeDTO:
    if settings.is_admin(tg_id):
        return MeDTO(role=Role.admin.value)

    driver = (
        await drivers_service.get_driver_by_tg(session, tg_id)
        if tg_id is not None
        else None
    )
    if driver is not None:
        return MeDTO(role=Role.driver.value, driver=await _driver_dto(session, driver))

    return MeDTO(role=Role.guest.value)
