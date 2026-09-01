"""Поля, потерянные при переезде бота на HTTP, и восстановленные обратно.

Каждое из них — наблюдаемое поведение старого бота, а не украшение.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db.models import Car, Driver, PaymentSchedule, SchedulePeriod


async def _driver_with_schedule(session, *, plate="01KG909AAA", days_ago=0):
    car = Car(plate=plate)
    session.add(car)
    await session.flush()
    driver = Driver(
        tg_user_id=8000 + car.id,
        full_name="Иван Водитель",
        phone="+996700000000",
        inn="12345678901234",
        car_id=car.id,
        active=True,
    )
    session.add(driver)
    await session.flush()
    session.add(
        PaymentSchedule(
            driver_id=driver.id,
            period=SchedulePeriod.weekly,
            amount=Decimal("1000.00"),
            paid_in_period=Decimal("0.00"),
            next_due_date=datetime.now(timezone.utc) - timedelta(days=days_ago),
            active=True,
        )
    )
    await session.commit()
    return car, driver


async def test_by_driver_report_keeps_car_plate(client, session):
    """В выписке по водителям машина была видна и должна остаться."""
    car, driver = await _driver_with_schedule(session)

    response = await client.get("/reports/by-driver")

    rows = response.json()
    assert rows[0]["driver_name"] == "Иван Водитель"
    assert rows[0]["car_plate"] == car.plate


async def test_payment_result_reports_prepayment(client, session):
    """Переплата закрывает периоды и оставляет предоплату в следующем."""
    car, driver = await _driver_with_schedule(session, days_ago=1)

    response = await client.post(
        "/payments",
        json={"driver_id": driver.id, "amount": "2500.00", "receipt_hash": "h1"},
    )

    body = response.json()
    assert body["periods_closed"] == 2
    assert Decimal(body["paid_in_period"]) == Decimal("500.00")


async def test_reminders_plan_force_bypasses_antispam(client, session):
    """Без force повторный прогон за день пуст — с force снова считает."""
    car, driver = await _driver_with_schedule(session, days_ago=3)

    first = await client.get("/reminders/plan")
    ids = [r["schedule_id"] for r in first.json()["reminders"]]
    assert ids, "просроченный график должен попасть в план"

    # Дату не передаём: её ставит сервер по таймзоне парка (иначе антиспам врёт).
    await client.post("/reminders/mark", json={"schedule_ids": ids})

    repeat = await client.get("/reminders/plan")
    assert repeat.json()["reminders"] == []

    forced = await client.get("/reminders/plan", params={"force": 1})
    assert [r["schedule_id"] for r in forced.json()["reminders"]] == ids


async def test_payment_and_schedule_land_in_one_transaction(client, session, monkeypatch):
    """Если применение к графику падает, платёж не должен остаться записанным."""
    from sqlalchemy import func, select

    from app.db.models import Payment
    from app.routers import payments as payments_router

    car, driver = await _driver_with_schedule(session, plate="01KG505AAA")

    async def _boom(*args, **kwargs):
        raise RuntimeError("сбой применения к графику")

    monkeypatch.setattr(payments_router.sched, "apply_payment", _boom)

    try:
        await client.post(
            "/payments",
            json={"driver_id": driver.id, "amount": "1000.00", "receipt_hash": "tx1"},
        )
    except RuntimeError:
        pass  # исключение всплывает наружу — важно, что данных не осталось

    session.expire_all()
    count = await session.scalar(select(func.count()).select_from(Payment))
    assert count == 0, "платёж без обновления графика сохраняться не должен"


async def test_upcoming_row_carries_overdue_days(client, session):
    """Клиенту нужен день срока отдельно от просрочки — иначе отчёт врёт."""
    car, driver = await _driver_with_schedule(session, plate="01KG606CCC", days_ago=0)

    rows = (await client.get("/reports/upcoming")).json()

    assert rows[0]["overdue_days"] == 0
    assert rows[0]["is_overdue"] is True, "платить надо сегодня"
