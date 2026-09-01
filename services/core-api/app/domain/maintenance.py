"""ТО по пробегу: список, привязка базы к трекеру, отметка выполнения (plan/02, plan/06)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CarState, MaintenanceItem


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: object) -> Decimal:
    # Значения приходят то Decimal (из БД/контракта), то float/int — нормализуем.
    return Decimal(str(value))


async def list_items(session: AsyncSession, car_id: int) -> list[MaintenanceItem]:
    result = await session.scalars(
        select(MaintenanceItem)
        .where(MaintenanceItem.car_id == car_id)
        .order_by(MaintenanceItem.type)
    )
    return list(result.all())


async def get_item(
    session: AsyncSession, car_id: int, mtype: str
) -> MaintenanceItem | None:
    return await session.scalar(
        select(MaintenanceItem).where(
            MaintenanceItem.car_id == car_id, MaintenanceItem.type == mtype
        )
    )


async def _get_or_create_car_state(session: AsyncSession, car_id: int) -> CarState:
    state = await session.get(CarState, car_id)
    if state is None:
        # Телеметрии по машине ещё не было — заводим снимок с нулевым пробегом.
        state = CarState(car_id=car_id, odometer_trusted=True)
        session.add(state)
        await session.flush()
    return state


async def upsert_item(
    session: AsyncSession,
    car_id: int,
    *,
    type: str,
    interval_km: Decimal,
    last_service_km: Decimal | None = None,
    note: str | None = None,
    created_by: int | None = None,
) -> MaintenanceItem:
    state = await _get_or_create_car_state(session, car_id)
    item = await get_item(session, car_id, type)
    if item is None:
        item = MaintenanceItem(car_id=car_id, type=type)
        session.add(item)

    item.interval_km = interval_km
    item.last_service_km = (
        last_service_km if last_service_km is not None else _dec(state.odometer_km or 0)
    )
    # База пробега привязана к трекеру — иначе смену устройства не поймать (S1).
    item.last_service_tracker_id = state.odometer_tracker_id
    item.note = note
    item.created_by = created_by

    await session.commit()
    await session.refresh(item)
    return item


async def mark_done(
    session: AsyncSession, car_id: int, mtype: str
) -> MaintenanceItem | None:
    item = await get_item(session, car_id, mtype)
    if item is None:
        return None
    state = await _get_or_create_car_state(session, car_id)

    item.last_service_km = _dec(state.odometer_km or 0)
    item.last_service_tracker_id = state.odometer_tracker_id
    item.last_service_at = _now()
    # Явная переустановка базы — единственный способ вернуть доверие к одометру (S2/S3).
    state.odometer_trusted = True

    await session.commit()
    await session.refresh(item)
    await session.refresh(state)
    return item


def over_km(item: MaintenanceItem, car_state: CarState | None) -> Decimal | None:
    if car_state is None or car_state.odometer_km is None:
        return None
    return _dec(car_state.odometer_km) - _dec(item.last_service_km) - _dec(item.interval_km)
