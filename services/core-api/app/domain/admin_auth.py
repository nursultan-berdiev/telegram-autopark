"""Вход в веб-админку по одноразовой ссылке из бота.

Пароля у админов нет и заводить его не хочется: личность уже подтверждена
Telegram, а бот знает, кто админ. Поэтому бот выдаёт одноразовую ссылку с
коротким сроком жизни, а сессию держит подписанная кука.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminLoginToken

TOKEN_TTL = timedelta(minutes=15)
# Столько живёт сессия в куке. Сутки: админка правит расписания, а не деньги.
SESSION_TTL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    """Хеш, а не сам токен: утечка таблицы не должна давать вход."""
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(session: AsyncSession, tg_user_id: int) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = _now() + TOKEN_TTL
    # Прошлые ссылки этого админа обесцениваем: выданная и забытая в чате
    # ссылка не должна работать неделю спустя.
    # synchronize_session=False: массовое удаление считает условие в БД, а не
    # перебором объектов сессии в Python.
    await session.execute(
        delete(AdminLoginToken).where(
            AdminLoginToken.tg_user_id == tg_user_id, AdminLoginToken.used_at.is_(None)
        ),
        execution_options={"synchronize_session": False},
    )
    session.add(
        AdminLoginToken(
            token_hash=hash_token(token),
            tg_user_id=tg_user_id,
            expires_at=expires_at,
        )
    )
    await session.commit()
    return token, expires_at


async def redeem_token(session: AsyncSession, token: str) -> int | None:
    """Возвращает Telegram-id админа или None, если ссылка не годится.

    Причину не различаем намеренно: подсказывать, «просрочена» ссылка или
    «не существует», значит помогать перебирать.
    """
    row = await session.scalar(
        select(AdminLoginToken).where(AdminLoginToken.token_hash == hash_token(token))
    )
    if row is None or row.used_at is not None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < _now():
        return None
    row.used_at = _now()
    await session.commit()
    return row.tg_user_id


async def purge_expired(session: AsyncSession) -> int:
    result = await session.execute(
        delete(AdminLoginToken).where(AdminLoginToken.expires_at < _now()),
        execution_options={"synchronize_session": False},
    )
    await session.commit()
    return int(result.rowcount or 0)
