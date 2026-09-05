"""Роутер invitations: создание и проверка одноразовых ссылок регистрации."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.config import settings
from app.db.session import get_session
from app.domain import cars as cars_service
from app.domain import invitations as invitations_service
from app.errors import NotFound
from contracts import InvitationDTO, InviteCheckDTO

router = APIRouter()


class InvitationCreate(BaseModel):
    """Локальная модель запроса — в contracts DTO есть только для ответа."""

    car_id: int


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.post("/invitations", response_model=InvitationDTO)
async def create_invitation(
    payload: InvitationCreate,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> InvitationDTO:
    car = await cars_service.get_car(session, payload.car_id)
    if car is None:
        raise NotFound(f"машина {payload.car_id} не найдена")

    invitation = await invitations_service.create_invitation(
        session, car_id=payload.car_id, created_by=actor
    )
    return InvitationDTO(
        code=invitation.code,
        car_id=invitation.car_id,
        expires_at=_utc(invitation.expires_at),
        ttl_label=settings.invite_ttl_label,  # TTL — сервер, не клиент (plan/03)
    )


@router.get("/invitations/resolve", response_model=InviteCheckDTO)
async def resolve_invitation(
    code: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> InviteCheckDTO:
    check = await invitations_service.resolve_invitation(session, code)

    car_plate: str | None = None
    car_id: int | None = None
    if check.invitation is not None:
        car_id = check.invitation.car_id
        car = await cars_service.get_car(session, car_id)
        car_plate = car.plate if car else None

    return InviteCheckDTO(
        ok=check.ok,
        problem=check.problem.value if check.problem else None,
        car_id=car_id,
        car_plate=car_plate,
    )
