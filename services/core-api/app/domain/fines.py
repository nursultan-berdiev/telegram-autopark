"""Штрафы: список, добавление, оплата, удаление, подсчёт неоплаченных для правил."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Driver, Fine, FineStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_start(window_days: int | None) -> datetime | None:
    # Окно — от now назад по issued_at (plan/06: fines_count.window_days).
    if window_days is None:
        return None
    return _now() - timedelta(days=window_days)


async def list_fines(
    session: AsyncSession,
    car_id: int,
    *,
    only_unpaid: bool = False,
    window_days: int | None = None,
) -> list[Fine]:
    stmt = select(Fine).where(Fine.car_id == car_id)
    if only_unpaid:
        stmt = stmt.where(Fine.status == FineStatus.unpaid)
    start = _window_start(window_days)
    if start is not None:
        stmt = stmt.where(Fine.issued_at >= start)
    result = await session.scalars(stmt.order_by(Fine.issued_at.desc()))
    return list(result.all())


async def get_fine(session: AsyncSession, fine_id: int) -> Fine | None:
    return await session.get(Fine, fine_id)


async def add_fine(
    session: AsyncSession,
    car_id: int,
    *,
    driver_id: int | None = None,
    amount: Decimal | None = None,
    currency: str | None = None,
    issued_at: datetime | None = None,
    external_ref: str | None = None,
    note: str | None = None,
    created_by: int | None = None,
) -> Fine:
    if driver_id is None:
        # Водитель не указан — считаем, что за рулём был текущий закреплённый.
        driver_id = await session.scalar(
            select(Driver.id).where(Driver.car_id == car_id, Driver.active.is_(True))
        )
    fine = Fine(
        car_id=car_id,
        driver_id=driver_id,
        amount=amount,
        currency=currency,
        issued_at=issued_at or _now(),
        external_ref=external_ref,
        note=note,
        created_by=created_by,
    )
    session.add(fine)
    await session.commit()
    await session.refresh(fine)
    return fine


async def pay_fine(session: AsyncSession, fine_id: int) -> Fine | None:
    fine = await get_fine(session, fine_id)
    if fine is None:
        return None
    fine.status = FineStatus.paid
    fine.paid_at = _now()
    await session.commit()
    await session.refresh(fine)
    return fine


async def delete_fine(session: AsyncSession, fine_id: int) -> bool:
    fine = await get_fine(session, fine_id)
    if fine is None:
        return False
    await session.delete(fine)
    await session.commit()
    return True


async def count_unpaid(
    session: AsyncSession, car_id: int, *, window_days: int | None = None
) -> int:
    stmt = select(func.count(Fine.id)).where(
        Fine.car_id == car_id, Fine.status == FineStatus.unpaid
    )
    start = _window_start(window_days)
    if start is not None:
        stmt = stmt.where(Fine.issued_at >= start)
    return int(await session.scalar(stmt) or 0)
