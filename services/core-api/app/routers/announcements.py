"""Роутер announcements: публикация объявлений парка и их история.

Публикует и удаляет только админ (проверка по X-TG-User-Id на сервере); читают
и админ, и водители — история у всех одна.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import Announcement
from app.db.session import get_session
from app.domain import announcements as announcements_service
from app.errors import NotFound
from contracts import AnnouncementCreate, AnnouncementDTO

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _dto(announcement: Announcement) -> AnnouncementDTO:
    return AnnouncementDTO(
        id=announcement.id,
        text=announcement.text,
        created_by=announcement.created_by,
        recipients=announcement.recipients,
        created_at=_utc(announcement.created_at),
    )


@router.post("/announcements", response_model=AnnouncementDTO, status_code=201)
async def publish(
    payload: AnnouncementCreate,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> AnnouncementDTO:
    announcement = await announcements_service.publish(
        session, text=payload.text, created_by=actor
    )
    return _dto(announcement)


@router.get("/announcements", response_model=list[AnnouncementDTO])
async def list_announcements(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[AnnouncementDTO]:
    rows = await announcements_service.list_announcements(session, limit=limit)
    return [_dto(a) for a in rows]


@router.get("/announcements/{announcement_id}", response_model=AnnouncementDTO)
async def get_announcement(
    announcement_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> AnnouncementDTO:
    announcement = await announcements_service.get_announcement(session, announcement_id)
    if announcement is None:
        raise NotFound(f"объявление {announcement_id} не найдено")
    return _dto(announcement)


@router.delete("/announcements/{announcement_id}", status_code=204, response_model=None)
async def delete_announcement(
    announcement_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> Response:
    if not await announcements_service.delete_announcement(session, announcement_id):
        raise NotFound(f"объявление {announcement_id} не найдено")
    return Response(status_code=204)
