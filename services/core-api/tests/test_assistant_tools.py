"""Инструменты ИИ-ассистента (read-only) и переключение gateway на tool-use."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.assistant import tools
from app.clients import ai_gateway
from app.db.models import (
    AlertType,
    Car,
    CarState,
    Command,
    CommandStatus,
    CommandType,
    Driver,
    Fine,
    FineStatus,
    SchedulePeriod,
    Tracker,
    TrackerProvider,
)
from app.domain import alerts as alerts_domain
from app.domain import payments as pay
from app.domain import schedules as sched
from app.clients.ai_gateway import RecognizedReceipt

NOW = datetime.now(timezone.utc)


async def _car(session, *, plate, model="Sonata", online=True, engine_blocked=False, driver_name=None):
    car = Car(plate=plate, model=model)
    session.add(car)
    await session.flush()
    if driver_name:
        session.add(Driver(tg_user_id=hash(plate) % 10**9, full_name=driver_name, phone="+1", inn=plate, car_id=car.id, active=True))
    tracker = Tracker(car_id=car.id, provider=TrackerProvider.traccar, external_id="t" + plate)
    session.add(tracker)
    await session.flush()
    session.add(CarState(
        car_id=car.id, tracker_id=tracker.id,
        last_ts=NOW if online else NOW.replace(year=2020),
        speed_knots=0.0, ignition=False, motion=False, engine_blocked=engine_blocked,
    ))
    await session.commit()
    return car


async def test_fleet_overview(session):
    await _car(session, plate="01KG195API", driver_name="Иванов", online=True)
    await _car(session, plate="01KG999API", driver_name=None, online=False)

    ov = await tools.fleet_overview(session)
    assert ov["total"] == 2 and ov["free"] == 1 and ov["occupied"] == 1
    by_plate = {c["plate"]: c for c in ov["cars"]}
    assert by_plate["01KG195API"]["driver"] == "Иванов" and by_plate["01KG195API"]["online"] is True
    assert by_plate["01KG999API"]["free"] is True and by_plate["01KG999API"]["online"] is False


async def test_payments_status(session):
    car = await _car(session, plate="01KG195API", driver_name="Иванов")
    driver = await session.scalar(select(Driver).where(Driver.car_id == car.id))
    await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.daily, interval_days=None,
        amount=1800.0, next_due_date=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    rows = await tools.payments_status(session)
    assert len(rows) == 1 and rows[0]["plate"] == "01KG195API"
    assert rows[0]["amount"] == 1800.0 and "summary" in rows[0]


async def test_recent_payments_window(session):
    car = await _car(session, plate="01KG195API", driver_name="Иванов")
    driver = await session.scalar(select(Driver).where(Driver.car_id == car.id))
    rec = RecognizedReceipt(True, 1800.0, "KGS", None, None, None)
    await pay.create_payment(
        session, driver_id=driver.id, car_id=car.id, amount=1800.0, paid_at=None,
        receipt_file_id=None, receipt_path=None, receipt_hash="h1", recognized=rec,
    )
    await session.commit()
    recent = await tools.recent_payments(session, hours=24)
    assert recent["count"] == 1 and recent["truncated"] is False
    assert recent["payments"][0]["plate"] == "01KG195API" and recent["payments"][0]["amount"] == 1800.0
    # hours от LLM валидируется: огромное → клампится, не OverflowError.
    assert (await tools.recent_payments(session, hours=10**9))["window_hours"] == 720
    assert (await tools.recent_payments(session, hours=-5))["window_hours"] == 1


async def test_car_state_and_unknown_plate(session):
    await _car(session, plate="01KG195API", engine_blocked=True)
    st = await tools.car_state(session, "01KG195API")
    assert st["plate"] == "01KG195API" and st["online"] is True and st["engine_blocked"] is True
    assert "error" in await tools.car_state(session, "01KG000XXX")


async def test_fines(session):
    car = await _car(session, plate="01KG195API")
    session.add(Fine(car_id=car.id, issued_at=NOW, status=FineStatus.unpaid, external_ref="r1", article="ст.1", amount=1000.0))
    session.add(Fine(car_id=car.id, issued_at=NOW, status=FineStatus.paid, external_ref="r2", amount=500.0))
    await session.commit()
    rows = await tools.fines(session)
    assert len(rows) == 1 and rows[0]["plate"] == "01KG195API" and rows[0]["article"] == "ст.1"
    assert "error" in (await tools.fines(session, "01KG000XXX"))[0]


async def test_open_alerts(session):
    car = await _car(session, plate="01KG195API")
    await alerts_domain.raise_alert(session, car_id=car.id, atype=AlertType.overdue_payment, payload={}, text="просрочка")
    await session.commit()
    rows = await tools.open_alerts(session)
    assert len(rows) == 1 and rows[0]["plate"] == "01KG195API" and rows[0]["text"] == "просрочка"


async def test_blocked_cars(session):
    car1 = await _car(session, plate="01KG195API")  # взведён (armed)
    await _car(session, plate="01KG999API", engine_blocked=True)  # заглушён по телеметрии
    car3 = await _car(session, plate="01KG637APR")  # блок СНЯТ более поздним resume
    session.add(Command(car_id=car1.id, type=CommandType.engine_stop, status=CommandStatus.armed, source="overdue", created_at=NOW, updated_at=NOW))
    session.add(Command(car_id=car3.id, type=CommandType.engine_stop, status=CommandStatus.acked, source="overdue", created_at=NOW, updated_at=NOW))
    session.add(Command(car_id=car3.id, type=CommandType.engine_resume, status=CommandStatus.acked, source="manual", created_at=NOW, updated_at=NOW))
    await session.commit()
    by_plate = {r["plate"]: r for r in await tools.blocked_cars(session)}
    assert "взведён" in by_plate["01KG195API"]["state"] and by_plate["01KG195API"]["source"] == "overdue"
    assert by_plate["01KG999API"]["state"] == "заглушён"
    assert "01KG637APR" not in by_plate, "снятый resume блок не считается действующим"


# --- gateway: переключение на инструменты -----------------------------------


async def test_gateway_tools_payload(monkeypatch):
    """В режиме инструментов шлём mcp_servers+system_prompt (без снимка)."""
    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", True)
    monkeypatch.setattr(ai_gateway.settings, "mcp_url", "http://core/mcp/")
    monkeypatch.setattr(ai_gateway.settings, "mcp_token", "secret")
    captured = {}

    async def _fake_post(path, payload, **kw):
        captured["path"], captured["payload"] = path, payload
        return {"text": "ответ"}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _fake_post)

    out = await ai_gateway._answer_owner_query_http("кто оплатил сегодня?", "СНИМОК-НЕ-ШЛЁМ")
    assert out == "ответ"
    p = captured["payload"]
    assert p["prompt"] == "кто оплатил сегодня?"
    assert "mcp_servers" in p and p["mcp_servers"]["mcpServers"]["fleet"]["url"] == "http://core/mcp/"
    assert p["mcp_servers"]["mcpServers"]["fleet"]["headers"]["Authorization"] == "Bearer secret"
    assert p["allowed_tools"] == ["mcp__fleet"]
    assert "СНИМОК-НЕ-ШЛЁМ" not in str(p), "снимок не должен уходить в промпт"


async def test_gateway_fallback_on_tools_exception(monkeypatch):
    """Сбой tool-режима → фолбэк на снимок (ветка, которую просил ревью)."""
    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", True)
    monkeypatch.setattr(ai_gateway.settings, "mcp_url", "http://core/mcp/")
    monkeypatch.setattr(ai_gateway.settings, "mcp_token", "secret")
    calls = []

    async def _post(path, payload, **kw):
        calls.append(payload)
        if "mcp_servers" in payload:
            raise RuntimeError("gateway/mcp недоступен")
        return {"text": "из снимка"}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _post)
    out = await ai_gateway._answer_owner_query_http("вопрос", "ДАННЫЕ-СНИМКА")
    assert out == "из снимка"
    assert len(calls) == 2 and "ДАННЫЕ-СНИМКА" in calls[1]["prompt"]


async def test_gateway_fallback_to_snapshot_when_disabled(monkeypatch):
    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", False)
    captured = {}

    async def _fake_post(path, payload, **kw):
        captured["payload"] = payload
        return {"text": "ответ"}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _fake_post)
    out = await ai_gateway._answer_owner_query_http("вопрос", "ДАННЫЕ-СНИМКА")
    assert out == "ответ"
    assert "mcp_servers" not in captured["payload"]
    assert "ДАННЫЕ-СНИМКА" in captured["payload"]["prompt"], "выключено → старый снимок-путь"
