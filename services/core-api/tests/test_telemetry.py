"""Ингест телеметрии, снимок состояния и доверие к пробегу."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Car, CarState, Telemetry, Tracker, TrackerProvider
from app.domain import telemetry as telemetry_domain


async def _car_with_tracker(session, plate="01KG777AAA", external_id="9175358042"):
    car = Car(plate=plate)
    session.add(car)
    await session.flush()
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id=external_id
    )
    session.add(tracker)
    await session.commit()
    return car, tracker


def _point(external_id: str, **over) -> dict:
    now = datetime.now(timezone.utc)
    point = {
        "external_id": external_id,
        "ts": now.isoformat(),
        "server_ts": now.isoformat(),
        "lat": 42.87,
        "lon": 74.59,
        "speed_knots": 0.0,
        "valid": True,
        "ignition": False,
        "motion": False,
        "total_distance_km": "1000.000",
        "engine_blocked": False,
        "status_raw": "fffffbff",
    }
    point.update(over)
    return point


async def test_batch_writes_rows_and_state(client, session, ingest_headers):
    car, tracker = await _car_with_tracker(session)
    car_id, external_id = car.id, tracker.external_id

    response = await client.post(
        "/telemetry/batch", json=[_point(external_id)], headers=ingest_headers
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    session.expire_all()
    rows = list(await session.scalars(select(Telemetry)))
    assert len(rows) == 1
    state = await session.get(CarState, car_id)
    assert state is not None
    assert state.ignition is False
    assert Decimal(str(state.odometer_km)) == Decimal("1000.000")


async def test_unknown_device_is_not_an_error(client, session, ingest_headers):
    response = await client.post(
        "/telemetry/batch", json=[_point("нет-такого")], headers=ingest_headers
    )

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 0
    assert body["unknown_devices"] == ["нет-такого"]


async def test_ingest_requires_ingest_token(client, session, admin_headers):
    """CORE_API_TOKEN в ингест не пускаем — области токенов разделены."""
    car, tracker = await _car_with_tracker(session)

    response = await client.post(
        "/telemetry/batch", json=[_point(tracker.external_id)], headers=admin_headers
    )

    assert response.status_code == 403


async def test_state_online_computed_from_last_ts(client, session):
    car, tracker = await _car_with_tracker(session)
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    session.add(CarState(car_id=car.id, tracker_id=tracker.id, last_ts=stale))
    await session.commit()

    response = await client.get(f"/cars/{car.id}/state")

    body = response.json()
    assert body["online"] is False
    assert body["last_point_age_seconds"] > 3600


async def test_odometer_drop_marks_untrusted(session):
    """Счётчик трекера не может уменьшиться — значит, устройство пересоздали."""
    car, tracker = await _car_with_tracker(session)
    await telemetry_domain.apply_point(
        session, tracker, {**_point(tracker.external_id), "total_distance_km": "5000"}
    )
    await session.commit()

    await telemetry_domain.apply_point(
        session, tracker, {**_point(tracker.external_id), "total_distance_km": "10"}
    )
    await session.commit()

    state = await session.get(CarState, car.id)
    assert state.odometer_trusted is False


async def test_old_point_does_not_rewind_state(session):
    car, tracker = await _car_with_tracker(session)
    now = datetime.now(timezone.utc)
    await telemetry_domain.apply_point(
        session,
        tracker,
        {**_point(tracker.external_id), "ts": now, "speed_knots": 30.0},
    )
    await session.commit()

    await telemetry_domain.apply_point(
        session,
        tracker,
        {
            **_point(tracker.external_id),
            "ts": now - timedelta(minutes=10),
            "speed_knots": 0.0,
        },
    )
    await session.commit()

    state = await session.get(CarState, car.id)
    assert state.speed_knots == pytest.approx(30.0)


async def test_future_device_clock_does_not_break_age(session):
    """Часы трекера ушли вперёд — возраст точки не должен быть отрицательным."""
    car, tracker = await _car_with_tracker(session)
    ahead = datetime.now(timezone.utc) + timedelta(minutes=3)
    session.add(CarState(car_id=car.id, tracker_id=tracker.id, last_ts=ahead))
    await session.commit()

    state = await session.get(CarState, car.id)

    assert telemetry_domain.point_age_seconds(state) == 0
    assert telemetry_domain.is_online(state) is True
