"""HTTP-тесты роутера payments: создание платежа, дубль чека, распознавание ИИ."""
from datetime import datetime, timezone

from app.clients import ai_gateway
from app.clients.ai_gateway import RecognizedReceipt
from app.db.models import SchedulePeriod
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import schedules as sched

UTC = timezone.utc


async def _driver_with_schedule(
    session, *, tg_id=1, plate="AA", amount=1000.0, next_due=None
):
    car = await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=tg_id, full_name="Водитель", phone="+1", inn="11111111",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.weekly, interval_days=None,
        amount=amount, next_due_date=next_due or datetime(2024, 6, 1, tzinfo=UTC),
    )
    return driver


async def test_partial_payment_holds_due_date(session, client):
    driver = await _driver_with_schedule(session, amount=1500.0)

    resp = await client.post("/payments", json={"driver_id": driver.id, "amount": "500.00"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["periods_closed"] == 0
    assert float(body["remaining_current"]) == 1000.0
    assert body["next_due_date"].startswith("2024-06-01")
    assert float(body["payment"]["amount"]) == 500.0

    # срок не сдвинулся — период ещё не закрыт (§92)
    sched_resp = await client.get(f"/drivers/{driver.id}/schedule")
    sched_body = sched_resp.json()
    assert float(sched_body["schedule"]["paid_in_period"]) == 500.0
    assert sched_body["schedule"]["next_due_date"].startswith("2024-06-01")


async def test_overpayment_closes_multiple_periods(session, client):
    driver = await _driver_with_schedule(session, tg_id=2, plate="BB", amount=1000.0)

    resp = await client.post("/payments", json={"driver_id": driver.id, "amount": "2500.00"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["periods_closed"] == 2
    assert float(body["remaining_current"]) == 500.0
    assert body["next_due_date"].startswith("2024-06-15")


async def test_payment_without_schedule_leaves_defaults(session, client):
    car = await cars_service.create_car(
        session, plate="NS1", model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=8, full_name="Без графика", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    resp = await client.post("/payments", json={"driver_id": driver.id, "amount": "100.00"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["periods_closed"] == 0
    assert body["next_due_date"] is None


async def test_duplicate_receipt_returns_409(session, client):
    driver = await _driver_with_schedule(session, tg_id=3, plate="CC")

    first = await client.post(
        "/payments",
        json={"driver_id": driver.id, "amount": "500.00", "receipt_hash": "hash-1"},
    )
    assert first.status_code == 200

    second = await client.post(
        "/payments",
        json={"driver_id": driver.id, "amount": "500.00", "receipt_hash": "hash-1"},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "чек уже был"

    payments = await client.get(f"/drivers/{driver.id}/payments")
    assert len(payments.json()) == 1


async def test_create_payment_driver_not_found(client):
    resp = await client.post("/payments", json={"driver_id": 999, "amount": "100.00"})
    assert resp.status_code == 404


async def test_list_driver_payments(session, client):
    driver = await _driver_with_schedule(session, tg_id=4, plate="DD", amount=1000.0)
    await client.post("/payments", json={"driver_id": driver.id, "amount": "300.00"})
    await client.post("/payments", json={"driver_id": driver.id, "amount": "400.00"})

    resp = await client.get(f"/drivers/{driver.id}/payments")
    assert resp.status_code == 200
    amounts = sorted(float(p["amount"]) for p in resp.json())
    assert amounts == [300.0, 400.0]


async def test_recognize_receipt_mocked_ai(monkeypatch, client):
    async def _fake_recognize(image_bytes, media_type):
        assert image_bytes == b"fake-bytes"
        assert media_type == "image/jpeg"
        return RecognizedReceipt(
            readable=True, amount=1500.5, currency="KGS",
            paid_at=datetime(2026, 7, 5, 14, 30, tzinfo=UTC),
            paid_at_raw="2026-07-05T14:30:00+00:00", note="ок",
        )

    # роутер вызывает ai_gateway.recognize_receipt как атрибут модуля — патчим там же.
    monkeypatch.setattr(ai_gateway, "recognize_receipt", _fake_recognize)

    resp = await client.post(
        "/payments/recognize",
        files={"file": ("receipt.jpg", b"fake-bytes", "image/jpeg")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert float(body["amount"]) == 1500.5
    assert body["currency"] == "KGS"
    assert body["paid_at"].startswith("2026-07-05T14:30:00")
    assert body["note"] == "ок"
    assert body["readable"] is True


async def test_recognize_receipt_unreadable(monkeypatch, client):
    async def _fake_recognize(image_bytes, media_type):
        return RecognizedReceipt(
            readable=False, amount=None, currency=None,
            paid_at=None, paid_at_raw=None, note="не похоже на чек",
        )

    monkeypatch.setattr(ai_gateway, "recognize_receipt", _fake_recognize)

    resp = await client.post(
        "/payments/recognize",
        files={"file": ("junk.png", b"not-a-receipt", "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["amount"] is None
    assert body["note"] == "не похоже на чек"
    assert body["readable"] is False
