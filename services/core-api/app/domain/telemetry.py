"""Приём телеметрии, снимок состояния машины и доверие к пробегу."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import CarState, Telemetry, Tracker

log = logging.getLogger(__name__)

# Ниже этого падения считаем, что счётчик трекера сбросили, а не «шумит».
ODOMETER_DROP_TOLERANCE_KM = Decimal("1")


def _aware(value: datetime | str | None) -> datetime | None:
    """SQLite отдаёт naive-время, а ингест — ISO-строки; приводим к UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def resolve_tracker(session: AsyncSession, external_id: str) -> Tracker | None:
    return await session.scalar(
        select(Tracker).where(
            Tracker.external_id == external_id, Tracker.active.is_(True)
        )
    )


async def get_state(session: AsyncSession, car_id: int) -> CarState | None:
    return await session.get(CarState, car_id)


def is_online(state: CarState | None, now: datetime | None = None) -> bool:
    """Онлайн — это свежесть последней точки, а не хранимый флаг."""
    if state is None or state.last_ts is None:
        return False
    now = now or datetime.now(timezone.utc)
    age = (now - _aware(state.last_ts)).total_seconds()  # type: ignore[operator]
    return abs(age) < settings.telemetry_stale_seconds


def point_age_seconds(state: CarState | None, now: datetime | None = None) -> int | None:
    """Часы трекера могут уйти вперёд — возраст точки не бывает отрицательным."""
    if state is None or state.last_ts is None:
        return None
    now = now or datetime.now(timezone.utc)
    age = (now - _aware(state.last_ts)).total_seconds()  # type: ignore[operator]
    return max(0, int(age))


async def apply_point(
    session: AsyncSession,
    tracker: Tracker,
    point: dict,
) -> Telemetry:
    """Пишет точку и обновляет снимок состояния машины."""
    ts = _aware(point.get("ts")) or datetime.now(timezone.utc)
    server_ts = _aware(point.get("server_ts")) or datetime.now(timezone.utc)
    distance = point.get("total_distance_km")
    distance = Decimal(str(distance)) if distance is not None else None

    row = Telemetry(
        car_id=tracker.car_id,
        tracker_id=tracker.id,
        ts=ts,
        server_ts=server_ts,
        lat=point.get("lat"),
        lon=point.get("lon"),
        speed_knots=point.get("speed_knots"),
        course=point.get("course"),
        altitude=point.get("altitude"),
        valid=bool(point.get("valid", True)),
        ignition=point.get("ignition"),
        motion=point.get("motion"),
        total_distance_km=distance,
        engine_blocked=point.get("engine_blocked"),
        status_raw=point.get("status_raw"),
        attributes=point.get("attributes"),
    )
    session.add(row)

    state = await session.get(CarState, tracker.car_id)
    if state is None:
        state = CarState(car_id=tracker.car_id)
        session.add(state)

    # Старая точка (запоздавшая) не должна откатывать снимок назад.
    if state.last_ts is not None and _aware(state.last_ts) > ts:  # type: ignore[operator]
        await session.flush()
        return row

    state.tracker_id = tracker.id
    state.last_ts = ts
    state.lat = point.get("lat", state.lat)
    state.lon = point.get("lon", state.lon)
    state.speed_knots = point.get("speed_knots")
    if point.get("ignition") is not None:
        state.ignition = point["ignition"]
    if point.get("motion") is not None:
        state.motion = point["motion"]
    if point.get("engine_blocked") is not None:
        state.engine_blocked = bool(point["engine_blocked"])

    if distance is not None:
        previous = Decimal(str(state.odometer_km)) if state.odometer_km is not None else None
        # Первое измерение: сравнивать не с чем — доверие не трогаем.
        first_measurement = state.odometer_tracker_id is None
        same_tracker = first_measurement or state.odometer_tracker_id == tracker.id
        if (
            previous is not None
            and same_tracker
            and distance < previous - ODOMETER_DROP_TOLERANCE_KM
        ):
            # Счётчик уехал вниз — устройство пересоздали или подменили.
            state.odometer_trusted = False
            log.warning(
                "пробег машины %s упал c %s до %s — база недостоверна",
                tracker.car_id,
                previous,
                distance,
            )
        if not same_tracker:
            state.odometer_trusted = False
        state.odometer_km = distance
        state.odometer_tracker_id = tracker.id

    state.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row


async def history(
    session: AsyncSession,
    car_id: int,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
) -> list[Telemetry]:
    query = select(Telemetry).where(Telemetry.car_id == car_id)
    if since is not None:
        query = query.where(Telemetry.ts >= since)
    if until is not None:
        query = query.where(Telemetry.ts <= until)
    query = query.order_by(Telemetry.ts.desc()).limit(limit)
    return list(await session.scalars(query))


async def cleanup(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Чистка ретеншена: телеметрия старше TELEMETRY_RETENTION_DAYS."""
    now = now or datetime.now(timezone.utc)
    edge = now - timedelta(days=settings.telemetry_retention_days)
    result = await session.execute(delete(Telemetry).where(Telemetry.ts < edge))
    await session.commit()
    return int(result.rowcount or 0)
