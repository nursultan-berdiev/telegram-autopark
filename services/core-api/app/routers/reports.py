"""Роутер reports: тонкий HTTP-слой над app.domain.reports (без своей логики)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.db.session import get_session
from app.domain import reports as reports_domain
from contracts import CarDriverRow, CarTotalRow, DriverTotalRow, UpcomingRow

router = APIRouter()


@router.get("/cars-drivers", response_model=list[CarDriverRow])
async def cars_drivers(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[CarDriverRow]:
    cars = await reports_domain.cars_with_drivers(session)
    return [
        CarDriverRow(
            plate=c.plate,
            model=c.model,
            status=c.status.value,
            driver_name=c.driver.full_name if c.driver else None,
            driver_phone=c.driver.phone if c.driver else None,
        )
        for c in cars
    ]


@router.get("/upcoming", response_model=list[UpcomingRow])
async def upcoming(
    days: int | None = Query(default=None, ge=0),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[UpcomingRow]:
    now = datetime.now(timezone.utc)
    items = await reports_domain.upcoming_payments(session, now)

    horizon = now + timedelta(days=days) if days is not None else None
    rows: list[UpcomingRow] = []
    for item in items:
        if horizon is not None and item.next_due > horizon:
            continue
        rows.append(
            UpcomingRow(
                driver_id=item.driver_id,
                driver_name=item.name,
                plate=None if item.car_plate == "—" else item.car_plate,
                next_due_date=item.next_due,
                amount=item.amount,
                debt_now=item.debt_now,
                is_overdue=item.is_overdue,
                overdue_days=item.overdue_days,
                summary=item.summary,
            )
        )
    return rows


@router.get("/by-driver", response_model=list[DriverTotalRow])
async def by_driver(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[DriverTotalRow]:
    totals = await reports_domain.statement_by_driver(session)
    return [
        DriverTotalRow(
            driver_id=t.driver_id,
            driver_name=t.name,
            car_plate=None if t.car_plate == "—" else t.car_plate,
            payments_count=t.count,
            total=t.total,
        )
        for t in totals
    ]


@router.get("/by-car", response_model=list[CarTotalRow])
async def by_car(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[CarTotalRow]:
    totals = await reports_domain.statement_by_car(session)
    return [
        CarTotalRow(car_id=t.car_id, plate=t.plate, payments_count=t.count, total=t.total)
        for t in totals
    ]
