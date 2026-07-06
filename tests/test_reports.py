"""Тесты отчётов и снимка данных (Этапы 5–6)."""
from datetime import datetime, timezone

from bot.db.models import SchedulePeriod
from bot.services import cars as cars_service
from bot.services import drivers as drivers_service
from bot.services import payments as pay
from bot.services import reports as rep
from bot.services import schedules as sched
from bot.services.ai import RecognizedReceipt


async def _seed(session):
    c1 = await cars_service.create_car(
        session, plate="AA", model="Cobalt", photo_file_id=None, photo_path=None
    )
    await cars_service.create_car(
        session, plate="BB", model=None, photo_file_id=None, photo_path=None
    )
    d = await drivers_service.register_driver(
        session, tg_user_id=1, full_name="Иванов", phone="+1", inn="11111111",
        selfie_file_id=None, selfie_path=None, car_id=c1.id,
    )
    await sched.set_schedule(
        session, driver_id=d.id, period=SchedulePeriod.weekly, interval_days=None,
        amount=1000.0, next_due_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    rec = RecognizedReceipt(True, 1000.0, "UZS", None, None, None)
    for amt, h in ((1000.0, "h1"), (500.0, "h2")):
        await pay.create_payment(
            session, driver_id=d.id, car_id=c1.id, amount=amt, paid_at=None,
            receipt_file_id=None, receipt_path=None, receipt_hash=h, recognized=rec,
        )
    return c1, d


async def test_cars_with_drivers(session):
    await _seed(session)
    cars = await rep.cars_with_drivers(session)
    mapping = {c.plate: (c.driver.full_name if c.driver else None) for c in cars}
    assert mapping == {"AA": "Иванов", "BB": None}


async def test_upcoming(session):
    await _seed(session)
    up = await rep.upcoming_payments(session)
    assert len(up) == 1 and up[0].name == "Иванов" and up[0].car_plate == "AA"


async def test_statements(session):
    await _seed(session)
    bd = await rep.statement_by_driver(session)
    assert bd[0].total == 1500.0 and bd[0].count == 2
    bc = {r.plate: (r.total, r.count) for r in await rep.statement_by_car(session)}
    assert bc["AA"] == (1500.0, 2) and bc["BB"] == (0.0, 0)


async def test_snapshot(session):
    await _seed(session)
    snap = await rep.build_snapshot(session)
    assert "Всего машин: 2" in snap
    assert "занято: 1" in snap and "свободно: 1" in snap
    assert "Иванов" in snap
