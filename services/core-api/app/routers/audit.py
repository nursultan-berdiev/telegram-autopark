"""Роутер audit: лента действий администратора (только чтение, только админ).

Строки пишет `app.audit.AuditMiddleware`; здесь — просмотр с фильтрами и
пагинацией для веб-админки.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor
from app.db.models import AuditLog
from app.db.session import get_session
from app.routers._dto import utc

router = APIRouter()


class AuditLogDTO(BaseModel):
    id: int
    actor_tg_user_id: int | None
    source: str
    method: str
    path: str
    status_code: int
    params: dict | None  # path-параметры маршрута (id машины/водителя и т.п.)
    created_at: datetime | None


@router.get("/audit", response_model=list[AuditLogDTO])
async def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: int | None = Query(default=None),
    source: Literal["bot", "web"] | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> list[AuditLogDTO]:
    """Лента действий админа (новые сверху) с фильтрами actor/source и пагинацией."""
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    if actor is not None:
        stmt = stmt.where(AuditLog.actor_tg_user_id == actor)
    if source:
        stmt = stmt.where(AuditLog.source == source)
    rows = (await session.scalars(stmt)).all()
    return [
        AuditLogDTO(
            id=r.id,
            actor_tg_user_id=r.actor_tg_user_id,
            source=r.source,
            method=r.method,
            path=r.path,
            status_code=r.status_code,
            params=r.params,
            created_at=utc(r.created_at),
        )
        for r in rows
    ]
