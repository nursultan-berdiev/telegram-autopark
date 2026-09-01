"""Операции с водителями: регистрация, карточка, увольнение, повторный наём."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Car, CarStatus, Driver, Payment, PaymentSchedule


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_driver_by_tg(session: AsyncSession, tg_user_id: int) -> Driver | None:
    """Активный водитель по telegram id (уволенный доступа не имеет)."""
    return await session.scalar(
        select(Driver).where(
            Driver.tg_user_id == tg_user_id, Driver.active.is_(True)
        )
    )


async def get_driver(session: AsyncSession, driver_id: int) -> Driver | None:
    return await session.scalar(
        select(Driver).options(selectinload(Driver.car)).where(Driver.id == driver_id)
    )


async def list_drivers(session: AsyncSession, *, active: bool = True) -> list[Driver]:
    """Список водителей: работающие (active=True) или уволенные (active=False)."""
    result = await session.scalars(
        select(Driver)
        .options(selectinload(Driver.car))
        .where(Driver.active.is_(active))
        .order_by(Driver.full_name)
    )
    return list(result.all())


@dataclass
class DriverStats:
    total_paid: float
    payments_count: int


async def driver_stats(session: AsyncSession, driver_id: int) -> DriverStats:
    """Сколько водитель заплатил за всё время (история переживает увольнение)."""
    row = await session.execute(
        select(
            func.coalesce(func.sum(Payment.amount), 0), func.count(Payment.id)
        ).where(Payment.driver_id == driver_id)
    )
    total, count = row.one()
    return DriverStats(total_paid=float(total), payments_count=int(count))


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
    """Регистрирует водителя и закрепляет машину (переводит её в occupied).

    Если этот telegram id уже есть в базе (уволенный водитель вернулся) —
    ОБНОВЛЯЕМ существующую запись, а не создаём новую: tg_user_id уникален,
    вставка упала бы с IntegrityError, и человек не смог бы устроиться заново.
    История его прошлых платежей при этом сохраняется.
    """
    driver = await session.scalar(
        select(Driver).where(Driver.tg_user_id == tg_user_id)
    )
    if driver is None:
        driver = Driver(tg_user_id=tg_user_id)
        session.add(driver)

    driver.full_name = full_name
    driver.phone = phone
    driver.inn = inn
    driver.selfie_file_id = selfie_file_id
    driver.selfie_path = selfie_path
    driver.car_id = car_id
    driver.active = True
    driver.fired_at = None

    car = await session.get(Car, car_id)
    if car is not None:
        car.status = CarStatus.occupied

    await session.commit()
    await session.refresh(driver)
    return driver


async def fire_driver(session: AsyncSession, driver: Driver) -> str | None:
    """Увольняет водителя: освобождает машину и останавливает график.

    Запись и платежи НЕ удаляются — водитель уходит в архив. Возвращает гос.
    номер освобождённой машины (или None).
    """
    plate: str | None = None
    if driver.car_id is not None:
        car = await session.get(Car, driver.car_id)
        if car is not None:
            car.status = CarStatus.free
            plate = car.plate
    driver.car_id = None
    driver.active = False
    driver.fired_at = _now()

    # График останавливаем — иначе продолжат идти напоминания и отчёты.
    schedule = await session.scalar(
        select(PaymentSchedule).where(PaymentSchedule.driver_id == driver.id)
    )
    if schedule is not None:
        schedule.active = False

    await session.commit()
    return plate
