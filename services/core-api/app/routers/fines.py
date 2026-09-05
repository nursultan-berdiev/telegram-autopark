"""Роутер fines: штрафы автомобиля (см. plan/03 §«Штрафы и ТО»)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import Fine
from app.db.session import get_session
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.errors import NotFound
from contracts import FineCreate, FineDTO

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _fine_dto(fine: Fine) -> FineDTO:
    return FineDTO(
        id=fine.id,
        car_id=fine.car_id,
        driver_id=fine.driver_id,
        amount=fine.amount,
        currency=fine.currency,
        issued_at=_utc(fine.issued_at),
        status=fine.status.value,
        paid_at=_utc(fine.paid_at),
        source=fine.source,
        external_ref=fine.external_ref,
        note=fine.note,
        created_at=_utc(fine.created_at),
    )


@router.get("/cars/{car_id}/fines", response_model=list[FineDTO])
async def list_car_fines(
    car_id: int,
    only_unpaid: bool = False,
    window_days: int | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[FineDTO]:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    fines = await fines_service.list_fines(
        session, car_id, only_unpaid=only_unpaid, window_days=window_days
    )
    return [_fine_dto(f) for f in fines]


@router.post("/cars/{car_id}/fines", response_model=FineDTO, status_code=201)
async def add_car_fine(
    car_id: int,
    payload: FineCreate,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> FineDTO:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    fine = await fines_service.add_fine(
        session,
        car_id,
        driver_id=payload.driver_id,
        amount=payload.amount,
        currency=payload.currency,
        issued_at=payload.issued_at,
        external_ref=payload.external_ref,
        note=payload.note,
        created_by=actor,
    )
    return _fine_dto(fine)


@router.post("/fines/{fine_id}/pay", response_model=FineDTO)
async def pay_car_fine(
    fine_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> FineDTO:
    fine = await fines_service.pay_fine(session, fine_id)
    if fine is None:
        raise NotFound(f"штраф {fine_id} не найден")
    return _fine_dto(fine)


@router.delete("/fines/{fine_id}", status_code=204, response_model=None)
async def delete_car_fine(
    fine_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> None:
    deleted = await fines_service.delete_fine(session, fine_id)
    if not deleted:
        raise NotFound(f"штраф {fine_id} не найден")
