"""Одноразовые приглашения водителей (deep-link, срок жизни из конфига)."""
from __future__ import annotations

import enum
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.models import Car, CarStatus, Invitation, InviteStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InviteProblem(str, enum.Enum):
    """Почему по ссылке нельзя зарегистрироваться.

    Причины разные, и водителю нужно говорить правду: «ссылка протухла» и
    «машину занял другой» требуют от него разных действий, а раньше на оба
    случая отвечали «срок действия истёк» — человек шёл просить новую ссылку
    там, где машины уже нет.
    """

    not_found = "not_found"  # кода не существует (опечатка, выдуманная ссылка)
    expired = "expired"  # срок жизни ссылки вышел
    used = "used"  # по ссылке уже зарегистрировался другой
    car_taken = "car_taken"  # ссылка жива, но машину успели занять


@dataclass(frozen=True)
class InviteCheck:
    invitation: Invitation | None = None
    problem: InviteProblem | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None and self.invitation is not None


async def resolve_invitation(session: AsyncSession, code: str) -> InviteCheck:
    """Проверяет ссылку и объясняет отказ.

    Занятость машины проверяется здесь же: два приглашения на одну машину —
    штатный сценарий, и второе становится бесполезным в тот момент, когда
    первый водитель завершил регистрацию, хотя сама ссылка ещё активна.
    """
    invitation = await session.scalar(select(Invitation).where(Invitation.code == code))
    if invitation is None:
        return InviteCheck(problem=InviteProblem.not_found)

    if invitation.status is InviteStatus.used:
        return InviteCheck(problem=InviteProblem.used)
    if invitation.status is InviteStatus.expired:
        return InviteCheck(problem=InviteProblem.expired)

    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        invitation.status = InviteStatus.expired  # помечаем лениво
        await session.commit()
        return InviteCheck(problem=InviteProblem.expired)

    car = await session.get(Car, invitation.car_id)
    if car is None or car.status is not CarStatus.free:
        return InviteCheck(problem=InviteProblem.car_taken)

    return InviteCheck(invitation=invitation)


async def create_invitation(
    session: AsyncSession, *, car_id: int, created_by: int
) -> Invitation:
    code = secrets.token_urlsafe(24)  # url-safe, ~32 симв., подходит для deep-link
    invitation = Invitation(
        code=code,
        car_id=car_id,
        created_by=created_by,
        expires_at=_now() + settings.invite_ttl,
        status=InviteStatus.active,
    )
    session.add(invitation)
    await session.commit()
    await session.refresh(invitation)
    return invitation


async def get_valid_invitation(
    session: AsyncSession, code: str
) -> Invitation | None:
    """Активное непросроченное приглашение на свободную машину, иначе None."""
    return (await resolve_invitation(session, code)).invitation


async def mark_used(session: AsyncSession, invitation: Invitation, used_by: int) -> None:
    invitation.status = InviteStatus.used
    invitation.used_by = used_by
    await session.commit()
