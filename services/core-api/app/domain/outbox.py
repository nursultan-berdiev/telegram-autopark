"""Очередь исходящих Telegram-сообщений (пишут celery-задачи, шлёт бот).

Разводит «кто считает» (celery) и «кто доставляет» (бот): celery не имеет доступа
в Telegram. Бот тянет `list_pending`, шлёт, помечает `mark_sent`; при сбое —
`mark_failed`. Постоянная ошибка (бот заблокирован/нет чата) `permanent=True` →
сразу `failed_at`; временная — счётчик попыток, лимит `MAX_ATTEMPTS` как
страховка от вечного ретрая. `failed_at ≠ sent_at`: потерянное уведомление видно.
At-least-once: отметку делает бот после отправки, поэтому редкий дубль возможен —
он предпочтён потере платёжного уведомления.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OutboundMessage

log = logging.getLogger(__name__)

# Страховка от бесконечного ретрая временной ошибки. При опросе раз в 30 с это
# ~10 минут попыток — краткий сбой Telegram/сети переживается, вечного цикла нет.
MAX_ATTEMPTS = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue(
    session: AsyncSession, *, recipient_tg_user_id: int, text: str, kind: str
) -> OutboundMessage:
    msg = OutboundMessage(
        recipient_tg_user_id=recipient_tg_user_id, text=text, kind=kind
    )
    session.add(msg)
    await session.flush()
    return msg


async def list_pending(
    session: AsyncSession, *, limit: int = 100
) -> list[OutboundMessage]:
    """Неотправленные и не «сдавшиеся» — старые первыми (FIFO-доставка)."""
    result = await session.scalars(
        select(OutboundMessage)
        .where(
            OutboundMessage.sent_at.is_(None),
            OutboundMessage.failed_at.is_(None),
        )
        .order_by(OutboundMessage.created_at)
        .limit(limit)
    )
    return list(result.all())


async def mark_sent(session: AsyncSession, message_id: int) -> bool:
    """True — сообщение существует (иначе роутер отдаст 404)."""
    msg = await session.get(OutboundMessage, message_id)
    if msg is None:
        return False
    if msg.sent_at is None:
        msg.sent_at = _now()
        await session.commit()
    return True


async def mark_failed(
    session: AsyncSession, message_id: int, *, permanent: bool = False
) -> bool:
    """Неудачная отправка. `permanent` (бот заблокирован/нет чата) → сразу сдаёмся;
    временная — +1 попытка, при достижении лимита тоже сдаёмся. True — id существует.
    """
    msg = await session.get(OutboundMessage, message_id)
    if msg is None:
        return False
    if msg.sent_at is None and msg.failed_at is None:
        # Атомарный инкремент (без read-modify-write гонки нескольких отправителей).
        await session.execute(
            update(OutboundMessage)
            .where(OutboundMessage.id == message_id)
            .values(attempts=OutboundMessage.attempts + 1)
        )
        await session.refresh(msg)
        if permanent or msg.attempts >= MAX_ATTEMPTS:
            msg.failed_at = _now()
            log.warning(
                "outbox %s: сдались (кому %s, попыток %s, permanent=%s)",
                message_id, msg.recipient_tg_user_id, msg.attempts, permanent,
            )
        await session.commit()
    return True
