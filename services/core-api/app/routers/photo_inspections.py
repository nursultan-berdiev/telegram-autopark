"""Роутер фотоконтроля: сдача осмотра водителем, статусы и история для админа.

Сдаёт только работающий водитель: личность — из X-TG-User-Id, машина — из его
записи, а не из тела запроса. Читает бот сервисным токеном.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core, tg_user_id
from app.db.session import get_session
from app.domain import photo_inspections as photo_service
from app.domain.photo_inspections import InspectionView, PhotoStatus
from app.errors import NotFound
from contracts import (
    PhotoInspectionCreate,
    PhotoInspectionDTO,
    PhotoInspectionResult,
    PhotoStatusDTO,
)

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dto(view: InspectionView) -> PhotoInspectionDTO:
    inspection = view.inspection
    return PhotoInspectionDTO(
        id=inspection.id,
        driver_id=inspection.driver_id,
        driver_name=view.driver_name,
        car_id=inspection.car_id,
        car_plate=view.car_plate,
        front_file_id=inspection.front_file_id,
        left_file_id=inspection.left_file_id,
        rear_file_id=inspection.rear_file_id,
        right_file_id=inspection.right_file_id,
        interior_front_file_id=inspection.interior_front_file_id,
        interior_rear_file_id=inspection.interior_rear_file_id,
        has_issues=inspection.has_issues,
        comment=inspection.comment,
        created_at=_utc(inspection.created_at),
    )


def _status_dto(status: PhotoStatus) -> PhotoStatusDTO:
    return PhotoStatusDTO(
        driver_id=status.driver_id,
        driver_name=status.driver_name,
        car_id=status.car_id,
        car_plate=status.car_plate,
        next_due_on=status.next_due_on,
        overdue_days=status.overdue_days,
        last_inspection_id=status.last_inspection_id,
        last_inspection_at=_utc(status.last_inspection_at),
    )


@router.post("/photo-inspections", response_model=PhotoInspectionResult, status_code=201)
async def submit(
    payload: PhotoInspectionCreate,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
    actor: int | None = Depends(tg_user_id),
) -> PhotoInspectionResult:
    """Сдача осмотра водителем. 201 — принят (или повтор в окне); 403 — актор не
    работающий водитель; 409 — за водителем нет машины; 422 — невалидные фото/отчёт."""
    inspection, schedule = await photo_service.submit(
        session,
        tg_user_id=actor,
        front_file_id=payload.front_file_id,
        left_file_id=payload.left_file_id,
        rear_file_id=payload.rear_file_id,
        right_file_id=payload.right_file_id,
        interior_front_file_id=payload.interior_front_file_id,
        interior_rear_file_id=payload.interior_rear_file_id,
        has_issues=payload.has_issues,
        comment=payload.comment,
        now=_now(),
    )
    view = await photo_service.get_inspection(session, inspection.id)
    return PhotoInspectionResult(inspection=_dto(view), next_due_on=schedule.next_due_on)


# Раньше /photo-inspections/{inspection_id}: иначе «status» разбирался бы как id.
@router.get("/photo-inspections/status", response_model=list[PhotoStatusDTO])
async def fleet_status(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[PhotoStatusDTO]:
    """Статус фотоконтроля всех работающих водителей с машиной — экран админа."""
    return [_status_dto(s) for s in await photo_service.statuses(session, _now())]


@router.get("/photo-inspections/{inspection_id}", response_model=PhotoInspectionDTO)
async def get_inspection(
    inspection_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> PhotoInspectionDTO:
    """Один осмотр с подписью (водитель, номер); 404 — нет такого."""
    view = await photo_service.get_inspection(session, inspection_id)
    if view is None:
        raise NotFound(f"осмотр {inspection_id} не найден")
    return _dto(view)


@router.get("/drivers/{driver_id}/photo-status", response_model=PhotoStatusDTO)
async def driver_status(
    driver_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> PhotoStatusDTO:
    """Статус одного водителя — экран водителя; 404 — уволен или без машины."""
    found = await photo_service.statuses(session, _now(), driver_id=driver_id)
    if not found:
        raise NotFound("нет работающего водителя с машиной")
    return _status_dto(found[0])


@router.get("/drivers/{driver_id}/photo-inspections", response_model=list[PhotoInspectionDTO])
async def driver_inspections(
    driver_id: int,
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[PhotoInspectionDTO]:
    """Последние осмотры водителя, свежие первыми (limit 1..50)."""
    rows = await photo_service.list_inspections(session, driver_id, limit=limit)
    return [_dto(view) for view in rows]
