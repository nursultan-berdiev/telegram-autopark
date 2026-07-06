"""Операции с автомобилями (справочник автопарка)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Car, CarStatus


async def list_cars(session: AsyncSession) -> list[Car]:
    result = await session.scalars(select(Car).order_by(Car.id))
    return list(result.all())


async def list_free_cars(session: AsyncSession) -> list[Car]:
    result = await session.scalars(
        select(Car).where(Car.status == CarStatus.free).order_by(Car.id)
    )
    return list(result.all())


async def get_car(session: AsyncSession, car_id: int) -> Car | None:
    return await session.get(Car, car_id)


async def plate_exists(session: AsyncSession, plate: str) -> bool:
    existing = await session.scalar(select(Car.id).where(Car.plate == plate))
    return existing is not None


async def create_car(
    session: AsyncSession,
    *,
    plate: str,
    model: str | None,
    photo_file_id: str | None,
    photo_path: str | None,
) -> Car:
    car = Car(
        plate=plate,
        model=model,
        photo_file_id=photo_file_id,
        photo_path=photo_path,
        status=CarStatus.free,
    )
    session.add(car)
    await session.commit()
    await session.refresh(car)
    return car


async def delete_car(session: AsyncSession, car: Car) -> None:
    await session.delete(car)
    await session.commit()
