"""Роутер maintenance: обслуживание по пробегу (см. plan/03 §«Штрафы и ТО»)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import CarState, MaintenanceItem
from app.db.session import get_session
from app.domain import cars as cars_service
from app.domain import maintenance as maint_service
from app.errors import NotFound
from contracts import MaintenanceDTO, MaintenanceUpsert

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _maintenance_dto(item: MaintenanceItem, car_state: CarState | None) -> MaintenanceDTO:
    return MaintenanceDTO(
        id=item.id,
        car_id=item.car_id,
        type=item.type,
        interval_km=item.interval_km,
        last_service_km=item.last_service_km,
        last_service_tracker_id=item.last_service_tracker_id,
        last_service_at=_utc(item.last_service_at),
        note=item.note,
        over_km=maint_service.over_km(item, car_state),
    )


@router.get("/cars/{car_id}/maintenance", response_model=list[MaintenanceDTO])
async def list_car_maintenance(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[MaintenanceDTO]:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    items = await maint_service.list_items(session, car_id)
    car_state = await session.get(CarState, car_id)
    return [_maintenance_dto(i, car_state) for i in items]


@router.put("/cars/{car_id}/maintenance", response_model=MaintenanceDTO)
async def put_car_maintenance(
    car_id: int,
    payload: MaintenanceUpsert,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> MaintenanceDTO:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    item = await maint_service.upsert_item(
        session,
        car_id,
        type=payload.type,
        interval_km=payload.interval_km,
        last_service_km=payload.last_service_km,
        note=payload.note,
        created_by=actor,
    )
    car_state = await session.get(CarState, car_id)
    return _maintenance_dto(item, car_state)


@router.post("/cars/{car_id}/maintenance/{mtype}/done", response_model=MaintenanceDTO)
async def mark_car_maintenance_done(
    car_id: int,
    mtype: str,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> MaintenanceDTO:
    car = await cars_service.get_car(session, car_id)
    if car is None:
        raise NotFound(f"машина {car_id} не найдена")
    item = await maint_service.mark_done(session, car_id, mtype)
    if item is None:
        raise NotFound(f"ТО типа {mtype} для машины {car_id} не найдено")
    car_state = await session.get(CarState, car_id)
    return _maintenance_dto(item, car_state)
