"""Ингест телеметрии от адаптера и чтение состояния машины."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core, require_ingest
from app.db.models import Car, CarState, Tracker
from app.db.session import get_session
from app.domain import commands as commands_domain
from app.domain import telemetry as telemetry_domain
from app.errors import NotFound
from app.rules import engine as rules_engine
from contracts import CarStateDTO, TelemetryBatchResult, TelemetryPoint

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/telemetry/batch", response_model=TelemetryBatchResult, status_code=202)
async def ingest_batch(
    points: list[TelemetryPoint],
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_ingest),
) -> TelemetryBatchResult:
    """Неизвестное устройство не ошибка адаптера: логируем и не роняем батч."""
    accepted = 0
    unknown: list[str] = []
    touched: list[int] = []
    for point in points:
        tracker = await telemetry_domain.resolve_tracker(session, point.external_id)
        if tracker is None:
            if point.external_id not in unknown:
                unknown.append(point.external_id)
            continue
        await telemetry_domain.apply_point(session, tracker, point.model_dump())
        if tracker.car_id not in touched:
            touched.append(tracker.car_id)
        accepted += 1

    await session.commit()

    if accepted:
        await commands_domain.confirm_by_telemetry(session, car_ids=touched)
        await rules_engine.evaluate_after_telemetry(session, car_ids=touched)

    if unknown:
        log.info("телеметрия от непривязанных устройств: %s", ", ".join(unknown))
    return TelemetryBatchResult(accepted=accepted, unknown_devices=unknown)


async def _car_or_404(session: AsyncSession, car_id: int) -> Car:
    car = await session.get(Car, car_id)
    if car is None:
        raise NotFound("машина не найдена")
    return car


def state_dto(
    car_id: int, state: CarState | None, now: datetime | None = None
) -> CarStateDTO:
    """online и возраст точки считаем при чтении — в БД их нет намеренно."""
    now = now or datetime.now(timezone.utc)
    if state is None:
        return CarStateDTO(car_id=car_id, online=False)
    return CarStateDTO(
        car_id=car_id,
        tracker_id=state.tracker_id,
        last_ts=state.last_ts,
        lat=state.lat,
        lon=state.lon,
        speed_knots=state.speed_knots,
        ignition=state.ignition,
        motion=state.motion,
        odometer_km=state.odometer_km,
        odometer_tracker_id=state.odometer_tracker_id,
        odometer_trusted=state.odometer_trusted,
        engine_blocked=state.engine_blocked,
        last_command=state.last_command,
        online=telemetry_domain.is_online(state, now),
        last_point_age_seconds=telemetry_domain.point_age_seconds(state, now),
    )


@router.get("/cars/{car_id}/state", response_model=CarStateDTO)
async def car_state(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> CarStateDTO:
    await _car_or_404(session, car_id)
    state = await telemetry_domain.get_state(session, car_id)
    return state_dto(car_id, state)


@router.get("/cars/{car_id}/telemetry", response_model=list[TelemetryPoint])
async def car_telemetry(
    car_id: int,
    since: datetime | None = Query(default=None, alias="from"),
    until: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=200, le=1000),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[TelemetryPoint]:
    await _car_or_404(session, car_id)
    rows = await telemetry_domain.history(
        session, car_id, since=since, until=until, limit=limit
    )
    external_by_tracker: dict[int, str] = {}
    for tracker_id in {row.tracker_id for row in rows if row.tracker_id}:
        tracker = await session.get(Tracker, tracker_id)
        if tracker is not None:
            external_by_tracker[tracker_id] = tracker.external_id
    return [
        TelemetryPoint(
            external_id=external_by_tracker.get(row.tracker_id or 0, ""),
            ts=row.ts,
            server_ts=row.server_ts,
            lat=row.lat,
            lon=row.lon,
            speed_knots=row.speed_knots,
            course=row.course,
            altitude=row.altitude,
            valid=row.valid,
            ignition=row.ignition,
            motion=row.motion,
            total_distance_km=row.total_distance_km,
            engine_blocked=row.engine_blocked,
            status_raw=row.status_raw,
            attributes=row.attributes,
        )
        for row in rows
    ]
