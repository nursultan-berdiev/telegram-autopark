"""HTTP-тесты роутера reports: тонкий слой поверх app.domain.reports."""
from datetime import datetime, timedelta, timezone

from app.clients.ai_gateway import RecognizedReceipt
from app.db.models import SchedulePeriod
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import payments as pay
from app.domain import schedules as sched

UTC = timezone.utc


async def _seed(session):
    car = await cars_service.create_car(
        session, plate="01A123BC", model="Cobalt", photo_file_id=None, photo_path=None
    )
    await cars_service.create_car(
        session, plate="02B456CD", model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=555, full_name="Иванов Иван", phone="+996700",
        inn="12345678", selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.weekly, interval_days=None,
        amount=1500.0, next_due_date=datetime(2020, 1, 1, tzinfo=UTC),
    )
    rec = RecognizedReceipt(True, 1500.0, "KGS", None, None, None)
    await pay.create_payment(
        session, driver_id=driver.id, car_id=car.id, amount=1500.0, paid_at=None,
        receipt_file_id=None, receipt_path=None, receipt_hash="h1", recognized=rec,
    )
    return car, driver


async def test_requires_auth(client):
    client.headers.pop("Authorization")
    resp = await client.get("/reports/cars-drivers")
    assert resp.status_code == 401


async def test_cars_drivers(client, session):
    car, driver = await _seed(session)
    resp = await client.get("/reports/cars-drivers")
    assert resp.status_code == 200
    rows = {r["plate"]: r for r in resp.json()}
    assert rows[car.plate]["driver_name"] == driver.full_name
    assert rows["02B456CD"]["driver_name"] is None
    assert rows["02B456CD"]["status"] == "free"


async def test_upcoming_overdue_maps_driver_and_car(client, session):
    car, driver = await _seed(session)
    resp = await client.get("/reports/upcoming")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["driver_id"] == driver.id
    assert row["driver_name"] == driver.full_name
    assert row["plate"] == car.plate
    assert row["is_overdue"] is True
    assert float(row["debt_now"]) > 0
    assert row["summary"]


async def test_upcoming_days_filter_excludes_far_due(client, session):
    await _seed(session)
    far_car = await cars_service.create_car(
        session, plate="FAR", model=None, photo_file_id=None, photo_path=None
    )
    far_driver = await drivers_service.register_driver(
        session, tg_user_id=777, full_name="Далёкий Водитель", phone="+996701",
        inn="87654321", selfie_file_id=None, selfie_path=None, car_id=far_car.id,
    )
    far_due = datetime.now(UTC) + timedelta(days=60)
    await sched.set_schedule(
        session, driver_id=far_driver.id, period=SchedulePeriod.weekly,
        interval_days=None, amount=500.0, next_due_date=far_due,
    )

    resp = await client.get("/reports/upcoming", params={"days": 7})
    assert resp.status_code == 200
    plates = [r["plate"] for r in resp.json()]
    assert "FAR" not in plates
    assert "01A123BC" in plates  # просроченный остаётся в любом горизонте


async def test_upcoming_no_car_plate_is_null(client, session):
    driver = await drivers_service.register_driver(
        session, tg_user_id=888, full_name="Без машины", phone="+996702",
        inn="11112222", selfie_file_id=None, selfie_path=None, car_id=None,
    )
    await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.weekly, interval_days=None,
        amount=200.0, next_due_date=datetime(2020, 1, 1, tzinfo=UTC),
    )
    resp = await client.get("/reports/upcoming")
    row = next(r for r in resp.json() if r["driver_id"] == driver.id)
    assert row["plate"] is None


async def test_by_driver(client, session):
    car, driver = await _seed(session)
    resp = await client.get("/reports/by-driver")
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["driver_id"] == driver.id)
    assert row["driver_name"] == driver.full_name
    assert row["payments_count"] == 1
    assert float(row["total"]) == 1500.0


async def test_by_car(client, session):
    car, driver = await _seed(session)
    resp = await client.get("/reports/by-car")
    assert resp.status_code == 200
    rows = {r["car_id"]: r for r in resp.json()}
    assert rows[car.id]["plate"] == car.plate
    assert rows[car.id]["payments_count"] == 1
    assert float(rows[car.id]["total"]) == 1500.0


# --------------------------------------------------------------------------
# /assistant/query — своего тестового файла у роутера нет (не в списке владения),
# по смыслу он ближе всего к отчётам: снимок для ИИ строит domain.reports.
# --------------------------------------------------------------------------


async def test_assistant_query_answers_from_snapshot(client, session, monkeypatch):
    car, driver = await _seed(session)

    captured = {}

    async def fake_answer(question: str, context_text: str) -> str:
        captured["question"] = question
        captured["context_text"] = context_text
        return "В парке 2 машины, 1 занята."

    from app.clients import ai_gateway

    monkeypatch.setattr(ai_gateway, "answer_owner_query", fake_answer)

    resp = await client.post(
        "/assistant/query", json={"question": "Сколько машин свободно?"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"answer": "В парке 2 машины, 1 занята."}
    assert captured["question"] == "Сколько машин свободно?"
    assert driver.full_name in captured["context_text"]  # снимок реально передан


async def test_assistant_requires_auth(client):
    client.headers.pop("Authorization")
    resp = await client.post("/assistant/query", json={"question": "?"})
    assert resp.status_code == 401
