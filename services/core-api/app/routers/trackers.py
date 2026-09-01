"""Привязка машина↔трекер.

Смена трекера обнуляет базу пробега: одометр принадлежит устройству,
а не машине (plan/02, R8).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import Car, CarState, Tracker, TrackerProvider
from app.db.session import get_session
from app.errors import Conflict, DomainError, NotFound
from contracts import TrackerDTO, TrackerUpsert

router = APIRouter()


def _provider(value: str) -> TrackerProvider:
    try:
        return TrackerProvider(value)
    except ValueError as exc:
        raise DomainError(f"неизвестный провайдер трекера: {value}", status_code=422) from exc


async def _car_or_404(session: AsyncSession, car_id: int) -> Car:
    car = await session.get(Car, car_id)
    if car is None:
        raise NotFound("машина не найдена")
    return car


@router.get("/cars/{car_id}/tracker", response_model=TrackerDTO | None)
async def get_tracker(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> TrackerDTO | None:
    await _car_or_404(session, car_id)
    tracker = await session.scalar(
        select(Tracker).where(Tracker.car_id == car_id, Tracker.active.is_(True))
    )
    return TrackerDTO.model_validate(tracker) if tracker else None


@router.put("/cars/{car_id}/tracker", response_model=TrackerDTO)
async def set_tracker(
    car_id: int,
    payload: TrackerUpsert,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> TrackerDTO:
    await _car_or_404(session, car_id)

    taken = await session.scalar(
        select(Tracker).where(
            Tracker.provider == _provider(payload.provider),
            Tracker.external_id == payload.external_id,
            Tracker.car_id != car_id,
            Tracker.active.is_(True),
        )
    )
    if taken is not None:
        raise Conflict("это устройство уже привязано к другой машине")

    tracker = await session.scalar(
        select(Tracker).where(Tracker.car_id == car_id, Tracker.active.is_(True))
    )
    if tracker is None:
        tracker = Tracker(
            car_id=car_id,
            provider=_provider(payload.provider),
            external_id=payload.external_id,
            config=payload.config,
        )
        session.add(tracker)
    else:
        tracker.provider = _provider(payload.provider)
        tracker.external_id = payload.external_id
        tracker.config = payload.config
    await session.flush()

    # База пробега снята с прежнего устройства — переустанавливаем от текущего.
    state = await session.get(CarState, car_id)
    if state is None:
        state = CarState(car_id=car_id, tracker_id=tracker.id)
        session.add(state)
    state.tracker_id = tracker.id
    state.odometer_km = None
    state.odometer_tracker_id = tracker.id
    # Пробег нового устройства неизвестен: обнулять базу ТО в ноль нельзя —
    # первая же точка с накопленным пробегом дала бы ложный алерт «пора ТО».
    # База остаётся старой и помечена недостоверной до отметки «ТО выполнено».
    state.odometer_trusted = False

    await session.commit()
    await session.refresh(tracker)
    return TrackerDTO.model_validate(tracker)


@router.delete("/cars/{car_id}/tracker", status_code=204)
async def delete_tracker(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> Response:
    await _car_or_404(session, car_id)
    tracker = await session.scalar(
        select(Tracker).where(Tracker.car_id == car_id, Tracker.active.is_(True))
    )
    if tracker is None:
        raise NotFound("трекер не привязан")
    # Не удаляем строку: на неё ссылается история телеметрии, команд и база ТО.
    tracker.active = False
    state = await session.get(CarState, car_id)
    if state is not None:
        state.odometer_trusted = False
    await session.commit()
    return Response(status_code=204)
