"""Роутер outbox: бот тянет неотправленные сообщения и помечает доставку.

Доставляет только бот (единственный с доступом в Telegram); сюда пишут
celery-задачи. Защищён сервисным токеном (`require_core`), как и алерты.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.db.session import get_session
from app.domain import outbox as outbox_domain
from contracts import OutboundMessageDTO

router = APIRouter()


@router.get("/outbox/pending", response_model=list[OutboundMessageDTO])
async def pending(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[OutboundMessageDTO]:
    rows = await outbox_domain.list_pending(session, limit=limit)
    return [
        OutboundMessageDTO(
            id=m.id,
            recipient_tg_user_id=m.recipient_tg_user_id,
            kind=m.kind,
            text=m.text,
        )
        for m in rows
    ]


@router.post("/outbox/{message_id}/sent", status_code=204, response_model=None)
async def sent(
    message_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> Response:
    if not await outbox_domain.mark_sent(session, message_id):
        raise HTTPException(status_code=404, detail="outbox: сообщение не найдено")
    return Response(status_code=204)


@router.post("/outbox/{message_id}/failed", status_code=204, response_model=None)
async def failed(
    message_id: int,
    permanent: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> Response:
    if not await outbox_domain.mark_failed(session, message_id, permanent=permanent):
        raise HTTPException(status_code=404, detail="outbox: сообщение не найдено")
    return Response(status_code=204)
