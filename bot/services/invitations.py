"""Одноразовые приглашения водителей (deep-link, срок жизни из конфига)."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import Invitation, InviteStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_invitation(
    session: AsyncSession, *, car_id: int, created_by: int
) -> Invitation:
    code = secrets.token_urlsafe(24)  # url-safe, ~32 симв., подходит для deep-link
    invitation = Invitation(
        code=code,
        car_id=car_id,
        created_by=created_by,
        expires_at=_now() + timedelta(hours=settings.invite_ttl_hours),
        status=InviteStatus.active,
    )
    session.add(invitation)
    await session.commit()
    await session.refresh(invitation)
    return invitation


async def get_valid_invitation(
    session: AsyncSession, code: str
) -> Invitation | None:
    """Возвращает активное непросроченное приглашение или None.

    Просроченные активные приглашения помечаются как expired (лениво).
    """
    invitation = await session.scalar(
        select(Invitation).where(Invitation.code == code)
    )
    if invitation is None:
        return None
    if invitation.status is not InviteStatus.active:
        return None

    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        invitation.status = InviteStatus.expired
        await session.commit()
        return None
    return invitation


async def mark_used(session: AsyncSession, invitation: Invitation, used_by: int) -> None:
    invitation.status = InviteStatus.used
    invitation.used_by = used_by
    await session.commit()
