"""HTTP-тесты роутера reminders: план для бота + антиспам-отметка."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.models import SchedulePeriod
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import schedules as sched

UTC = timezone.utc
NOW = datetime(2026, 7, 10, 3, tzinfo=UTC)  # срок 05.07 UTC — заведомая просрочка


async def _driver_with_schedule(session, *, next_due, tg_id=1, plate="AA"):
    car = await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=tg_id, full_name=f"Водитель {tg_id}", phone="+996700",
        inn="11111111", selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    schedule = await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.weekly, interval_days=None,
        amount=1500.0, next_due_date=next_due,
    )
    return driver, schedule


async def test_requires_auth(client):
    client.headers.pop("Authorization")
    resp = await client.get("/reminders/plan")
    assert resp.status_code == 401


async def test_plan_returns_overdue_reminder_with_driver_id(client, session):
    driver, schedule = await _driver_with_schedule(
        session, next_due=datetime(2026, 7, 5, tzinfo=UTC)
    )
    resp = await client.get("/reminders/plan", params={"now": NOW.isoformat()})
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["reminders"]) == 1
    item = body["reminders"][0]
    assert item["schedule_id"] == schedule.id
    assert item["driver_id"] == driver.id
    assert item["tg_user_id"] == driver.tg_user_id
    assert item["kind"] == "overdue"
    assert "Просрочка" in item["text"]

    assert body["owner_digest"]  # владельцу сводка тоже пришла
    expected_today = NOW.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
    assert body["today"] == expected_today


async def test_plan_default_now_is_current_time(client, session):
    """Без ?now= план строится на текущий момент (проверяем, что запрос не падает)."""
    await _driver_with_schedule(session, next_due=datetime(2020, 1, 1, tzinfo=UTC))
    resp = await client.get("/reminders/plan")
    assert resp.status_code == 200
    assert len(resp.json()["reminders"]) == 1


async def test_mark_removes_driver_from_next_plan(client, session):
    driver, schedule = await _driver_with_schedule(
        session, next_due=datetime(2026, 7, 5, tzinfo=UTC)
    )
    first = await client.get("/reminders/plan", params={"now": NOW.isoformat()})
    assert len(first.json()["reminders"]) == 1
    today = first.json()["today"]

    mark_resp = await client.post(
        "/reminders/mark",
        json={"schedule_ids": [schedule.id], "on_date": today},
    )
    assert mark_resp.status_code == 204

    second = await client.get("/reminders/plan", params={"now": NOW.isoformat()})
    assert second.json()["reminders"] == []
    # владельческая сводка не подчиняется антиспаму — водитель там остаётся
    assert second.json()["owner_digest"]


async def test_mark_empty_list_is_noop(client):
    resp = await client.post(
        "/reminders/mark", json={"schedule_ids": [], "on_date": "2026-07-10"}
    )
    assert resp.status_code == 204
