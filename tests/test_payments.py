"""Тесты платежей и распознавания (Этап 4)."""
import json
from datetime import datetime, timezone

from bot.db.models import PaymentStatus
from bot.services import cars as cars_service
from bot.services import drivers as drivers_service
from bot.services import payments as pay
from bot.services.ai import RecognizedReceipt, _parse_receipt


def test_receipt_hash():
    assert pay.receipt_hash(b"abc") == pay.receipt_hash(b"abc")
    assert pay.receipt_hash(b"abc") != pay.receipt_hash(b"xyz")


def test_parse_receipt():
    r = _parse_receipt(
        {
            "readable": True,
            "amount": "1500.50",
            "currency": "UZS",
            "paid_at": "2026-07-05T14:30:00",
        }
    )
    assert r.readable and r.amount == 1500.5 and r.currency == "UZS"
    assert r.paid_at == datetime(2026, 7, 5, 14, 30)


def test_parse_receipt_unreadable():
    r = _parse_receipt({"readable": False, "amount": None, "currency": None, "paid_at": None})
    assert r.readable is False and r.amount is None and r.paid_at is None


async def test_create_and_duplicate(session):
    car = await cars_service.create_car(
        session, plate="AA", model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=1, full_name="С", phone="+1", inn="11111111",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    rhash = pay.receipt_hash(b"receipt-bytes")
    assert await pay.is_duplicate(session, rhash) is False

    rec = RecognizedReceipt(
        True, 1500.0, "UZS", datetime(2026, 7, 5, tzinfo=timezone.utc),
        "2026-07-05", None,
    )
    payment = await pay.create_payment(
        session, driver_id=driver.id, car_id=car.id, amount=1500.0,
        paid_at=rec.paid_at, receipt_file_id="f", receipt_path="p",
        receipt_hash=rhash, recognized=rec,
    )
    assert payment.status == PaymentStatus.confirmed
    assert json.loads(payment.recognized_data)["amount"] == 1500.0
    assert await pay.is_duplicate(session, rhash) is True
    assert len(await pay.list_payments_by_driver(session, driver.id)) == 1
