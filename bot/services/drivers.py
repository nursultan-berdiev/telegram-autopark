"""Операции с водителями."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Car, CarStatus, Driver


async def get_driver_by_tg(session: AsyncSession, tg_user_id: int) -> Driver | None:
    return await session.scalar(
        select(Driver).where(Driver.tg_user_id == tg_user_id)
    )


async def list_drivers(session: AsyncSession) -> list[Driver]:
    result = await session.scalars(
        select(Driver).options(selectinload(Driver.car)).order_by(Driver.id)
    )
    return list(result.all())


async def register_driver(
    session: AsyncSession,
    *,
    tg_user_id: int,
    full_name: str,
    phone: str,
    inn: str,
    selfie_file_id: str | None,
    selfie_path: str | None,
    car_id: int,
) -> Driver:
    """Создаёт водителя и закрепляет за ним машину (переводит её в occupied).

    Одна транзакция: водитель + смена статуса машины.
    """
    driver = Driver(
        tg_user_id=tg_user_id,
        full_name=full_name,
        phone=phone,
        inn=inn,
        selfie_file_id=selfie_file_id,
        selfie_path=selfie_path,
        car_id=car_id,
    )
    session.add(driver)

    car = await session.get(Car, car_id)
    if car is not None:
        car.status = CarStatus.occupied

    await session.commit()
    await session.refresh(driver)
    return driver
