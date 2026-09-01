"""Команды на трекер: только админ, с гейтом и аудитом."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import Car, Command
from app.db.session import get_session
from app.domain import commands as commands_domain
from app.errors import NotFound
from contracts import CommandDTO, CommandRequest, CommandResult

router = APIRouter()


def _to_dto(command: Command) -> CommandDTO:
    return CommandDTO(
        id=command.id,
        car_id=command.car_id,
        tracker_id=command.tracker_id,
        type=command.type.value,
        status=command.status.value,
        requested_by=command.requested_by,
        alert_id=command.alert_id,
        safety_snapshot=command.safety_snapshot,
        result=command.result,
        created_at=command.created_at,
        acked_at=command.acked_at,
    )


@router.post("/cars/{car_id}/commands", response_model=CommandResult)
async def create_command(
    car_id: int,
    payload: CommandRequest,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> CommandResult:
    car = await session.get(Car, car_id)
    if car is None:
        raise NotFound("машина не найдена")

    try:
        command, ok, reason = await commands_domain.request_command(
            session,
            car_id=car_id,
            type_value=payload.type,
            requested_by=actor,
            alert_id=payload.alert_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(command)
    return CommandResult(command=_to_dto(command), ok=ok, reason=reason)


@router.get("/cars/{car_id}/commands", response_model=list[CommandDTO])
async def list_commands(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[CommandDTO]:
    car = await session.get(Car, car_id)
    if car is None:
        raise NotFound("машина не найдена")
    return [_to_dto(c) for c in await commands_domain.list_commands(session, car_id)]
