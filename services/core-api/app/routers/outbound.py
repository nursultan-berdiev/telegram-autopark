"""Роутер outbound: журнал очереди исходящих Telegram-сообщений (только админ).

Отдельно от `outbox.py` (тот — сервисный, для бота). Здесь — чтение со статусами
доставки для веб-админки, с пагинацией.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor
from app.db.models import OutboundMessage
from app.db.session import get_session
from app.routers._dto import utc

router = APIRouter()


class OutboundMessageAdminDTO(BaseModel):
    """Строка очереди для журнала веб-админки — со статусами доставки."""

    id: int
    recipient_tg_user_id: int
    kind: str
    text: str
    attempts: int
    created_at: datetime | None
    sent_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None


@router.get("/outbound-messages", response_model=list[OutboundMessageAdminDTO])
async def list_outbound(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> list[OutboundMessageAdminDTO]:
    """Очередь исходящих со статусом (отправлено/сдались/отменено) — для журнала."""
    stmt = (
        select(OutboundMessage)
        .order_by(OutboundMessage.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if kind:
        stmt = stmt.where(OutboundMessage.kind == kind)
    rows = (await session.scalars(stmt)).all()
    return [
        OutboundMessageAdminDTO(
            id=m.id,
            recipient_tg_user_id=m.recipient_tg_user_id,
            kind=m.kind,
            text=m.text,
            attempts=m.attempts,
            created_at=utc(m.created_at),
            sent_at=utc(m.sent_at),
            failed_at=utc(m.failed_at),
            cancelled_at=utc(m.cancelled_at),
        )
        for m in rows
    ]
