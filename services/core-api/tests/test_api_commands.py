"""HTTP-слой команд и алертов: права, разделение токенов, аудит."""
from datetime import datetime, timezone

import pytest

from app.db.models import Car, CarState, Tracker, TrackerProvider
from app.domain import commands as commands_domain


@pytest.fixture(autouse=True)
def adapter_ok(monkeypatch):
    async def _send(external_id: str, command: str, params=None):
        return {"status": "sent", "result": "S20,OK"}

    monkeypatch.setattr(commands_domain, "send_command", _send)


async def _parked_car(session):
    car = Car(plate="01KG222AAA")
    session.add(car)
    await session.flush()
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id="9175358042"
    )
    session.add(tracker)
    await session.flush()
    session.add(
        CarState(
            car_id=car.id,
            tracker_id=tracker.id,
            last_ts=datetime.now(timezone.utc),
            speed_knots=0.0,
            ignition=False,
            motion=False,
        )
    )
    await session.commit()
    return car.id


async def test_block_requires_admin(client, session):
    """requested_by из тела — не доказательство прав, проверяем на сервере."""
    car_id = await _parked_car(session)

    response = await client.post(
        f"/cars/{car_id}/commands",
        json={"type": "engine_block", "requested_by": 999},
        headers={"X-TG-User-Id": "999"},
    )

    assert response.status_code == 403


async def test_admin_can_block_parked_car(client, session, admin_headers):
    car_id = await _parked_car(session)

    response = await client.post(
        f"/cars/{car_id}/commands",
        json={"type": "engine_block", "requested_by": 111},
        headers=admin_headers,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["command"]["status"] == "sent"

    audit = await client.get(f"/cars/{car_id}/commands")
    assert len(audit.json()) == 1


async def test_ingest_token_cannot_command(client, session, ingest_headers):
    car_id = await _parked_car(session)

    response = await client.post(
        f"/cars/{car_id}/commands",
        json={"type": "engine_block", "requested_by": 111},
        headers={**ingest_headers, "X-TG-User-Id": "111"},
    )

    assert response.status_code == 403


async def test_unknown_command_is_422(client, session, admin_headers):
    car_id = await _parked_car(session)

    response = await client.post(
        f"/cars/{car_id}/commands",
        json={"type": "взлететь", "requested_by": 111},
        headers=admin_headers,
    )

    assert response.status_code == 422


async def test_command_on_missing_car_is_404(client, admin_headers):
    response = await client.post(
        "/cars/999/commands",
        json={"type": "engine_block", "requested_by": 111},
        headers=admin_headers,
    )

    assert response.status_code == 404


async def test_alerts_listing_and_ack(client, session, admin_headers):
    from app.db.models import AlertType
    from app.domain import alerts as alerts_domain

    car_id = await _parked_car(session)
    await alerts_domain.raise_alert(
        session,
        car_id=car_id,
        atype=AlertType.command_unconfirmed,
        payload={"command_id": 1},
        text="команда не подтверждена",
    )
    await session.commit()

    listing = await client.get("/alerts", params={"status": "open"})
    assert listing.status_code == 200
    alerts = listing.json()
    assert len(alerts) == 1
    assert alerts[0]["text"] == "команда не подтверждена"
    assert alerts[0]["car_plate"] == "01KG222AAA"

    acked = await client.post(f"/alerts/{alerts[0]['id']}/ack")
    assert acked.json()["status"] == "acknowledged"


async def test_rules_crud_is_admin_only(client, session, admin_headers):
    payload = {"type": "overdue_payment", "params": {"min_days": 1}}

    denied = await client.post("/rules", json=payload, headers={"X-TG-User-Id": "999"})
    assert denied.status_code == 403

    created = await client.post("/rules", json=payload, headers=admin_headers)
    assert created.status_code == 201

    listing = await client.get("/rules")
    assert len(listing.json()) == 1


async def test_car_and_schedule_writes_are_admin_only(client, session):
    """Общий токен бота есть у всех его пользователей — запись гейтим по админу."""
    created = await client.post("/cars", json={"plate": "01KG404AAA"})
    assert created.status_code == 403

    deleted = await client.delete("/cars/1")
    assert deleted.status_code == 403

    schedule = await client.put(
        "/drivers/1/schedule",
        json={
            "period": "weekly",
            "amount": "1000.00",
            "next_due_date": "2027-01-01T00:00:00+00:00",
        },
    )
    assert schedule.status_code == 403


async def test_invalid_enums_are_422_not_500(client, session, admin_headers):
    bad_rule = await client.post(
        "/rules", json={"type": "телепатия", "params": {}}, headers=admin_headers
    )
    assert bad_rule.status_code == 422

    bad_status = await client.get("/alerts", params={"status": "какой-то"})
    assert bad_status.status_code == 422
