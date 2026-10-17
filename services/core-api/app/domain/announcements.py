"""Объявления парка: публикация с рассылкой водителям и история.

Рассылку шлёт бот из очереди `outbound_messages`. Объявление и письма водителям
пишутся одной транзакцией: опубликованного без рассылки не бывает.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Announcement, Driver
from app.domain import outbox
from app.errors import Validation
from contracts import ANNOUNCEMENT_MAX_TEXT, render_announcement, telegram_length

log = logging.getLogger(__name__)

KIND = "announcement"  # outbound_messages.kind


async def publish(session: AsyncSession, *, text: str, created_by: int) -> Announcement:
    """Сохраняет объявление и ставит письмо каждому работающему водителю."""
    body = text.strip()
    if not body:
        raise Validation("пустое объявление")
    if telegram_length(body) > ANNOUNCEMENT_MAX_TEXT:
        raise Validation(f"объявление длиннее {ANNOUNCEMENT_MAX_TEXT} символов")

    recipients = list(
        await session.scalars(select(Driver.tg_user_id).where(Driver.active.is_(True)))
    )
    announcement = Announcement(
        text=body, created_by=created_by, recipients=len(recipients)
    )
    session.add(announcement)
    await session.flush()
    message = render_announcement(body)
    for tg_user_id in recipients:
        await outbox.enqueue(
            session,
            recipient_tg_user_id=tg_user_id,
            text=message,
            kind=KIND,
            announcement_id=announcement.id,
        )
    await session.commit()
    await session.refresh(announcement)
    return announcement


async def list_announcements(session: AsyncSession, *, limit: int = 50) -> list[Announcement]:
    """Свежие первыми; удалённые не показываем."""
    result = await session.scalars(
        select(Announcement)
        .where(Announcement.deleted_at.is_(None))
        .order_by(Announcement.created_at.desc(), Announcement.id.desc())
        .limit(limit)
    )
    return list(result.all())


async def get_announcement(session: AsyncSession, announcement_id: int) -> Announcement | None:
    announcement = await session.get(Announcement, announcement_id)
    if announcement is None or announcement.deleted_at is not None:
        return None
    return announcement


async def delete_announcement(session: AsyncSession, announcement_id: int) -> bool:
    """Скрывает из истории и снимает ещё не ушедшую рассылку. True — объявление было.

    Уже доставленные Telegram отозвать не даёт; письма, которые бот успел забрать
    в текущий проход, тоже уйдут (см. outbox.cancel_announcement).
    """
    announcement = await get_announcement(session, announcement_id)
    if announcement is None:
        return False
    now = datetime.now(timezone.utc)
    announcement.deleted_at = now
    cancelled = await outbox.cancel_announcement(session, announcement_id, now=now)
    await session.commit()
    if cancelled:
        log.info("объявление %s удалено, рассылка снята: %s", announcement_id, cancelled)
    return True
