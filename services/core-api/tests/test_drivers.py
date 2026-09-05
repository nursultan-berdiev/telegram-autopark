"""Увольнение водителя: архив, освобождение машины, потеря доступа, повторный наём."""
from datetime import datetime, timezone

from app.db.models import CarStatus, SchedulePeriod
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import payments as pay
from app.domain import reports as rep
from app.domain import schedules as sched
from app.clients.ai_gateway import RecognizedReceipt

UTC = timezone.utc


async def _hire(session, *, tg_id=1, plate="AA"):
    car = await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=tg_id, full_name="Иванов Иван", phone="+996700",
        inn="12345678", selfie_file_id="s", selfie_path="p", car_id=car.id,
    )
    return car, driver


async def test_fire_frees_car_and_archives_driver(session):
    car, driver = await _hire(session)
    plate = await drivers_service.fire_driver(session, driver)

    assert plate == "AA"
    assert driver.active is False and driver.fired_at is not None
    assert driver.car_id is None

    # машина снова свободна и доступна для нового водителя
    car_after = await cars_service.get_car(session, car.id)
    assert car_after.status == CarStatus.free
    assert [c.id for c in await cars_service.list_free_cars(session)] == [car.id]


async def test_fired_driver_loses_access(session):
    _, driver = await _hire(session, tg_id=777)
    assert await drivers_service.get_driver_by_tg(session, 777) is not None

    await drivers_service.fire_driver(session, driver)
    # RoleMiddleware ищет водителя этой же функцией → уволенный станет гостем
    assert await drivers_service.get_driver_by_tg(session, 777) is None


async def test_fire_stops_schedule(session):
    _, driver = await _hire(session)
    await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.weekly, interval_days=None,
        amount=1500.0, next_due_date=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert len(await rep.upcoming_payments(session)) == 1

    await drivers_service.fire_driver(session, driver)

    schedule = await sched.get_schedule(session, driver.id)
    assert schedule.active is False
    # из отчёта «кому скоро платить» и из напоминаний уволенный пропадает
    assert await rep.upcoming_payments(session) == []


async def test_payment_history_survives_firing(session):
    car, driver = await _hire(session)
    rec = RecognizedReceipt(True, 1000.0, "KGS", None, None, None)
    await pay.create_payment(
        session, driver_id=driver.id, car_id=car.id, amount=1000.0, paid_at=None,
        receipt_file_id=None, receipt_path=None, receipt_hash="h1", recognized=rec,
    )

    await drivers_service.fire_driver(session, driver)

    stats = await drivers_service.driver_stats(session, driver.id)
    assert stats.total_paid == 1000.0 and stats.payments_count == 1
    # и в выписке владельца платёж остаётся
    rows = {r.name: r.total for r in await rep.statement_by_driver(session)}
    assert rows["Иванов Иван"] == 1000.0


async def test_lists_split_active_and_fired(session):
    _, d1 = await _hire(session, tg_id=1, plate="AA")
    await _hire(session, tg_id=2, plate="BB")

    await drivers_service.fire_driver(session, d1)

    active = await drivers_service.list_drivers(session, active=True)
    fired = await drivers_service.list_drivers(session, active=False)
    assert [d.tg_user_id for d in active] == [2]
    assert [d.tg_user_id for d in fired] == [1]


async def test_rehire_reuses_record_and_keeps_history(session):
    """Уволенный вернулся: tg_user_id уникален — нельзя вставлять новую запись."""
    car, driver = await _hire(session, tg_id=555, plate="AA")
    rec = RecognizedReceipt(True, 500.0, "KGS", None, None, None)
    await pay.create_payment(
        session, driver_id=driver.id, car_id=car.id, amount=500.0, paid_at=None,
        receipt_file_id=None, receipt_path=None, receipt_hash="h1", recognized=rec,
    )
    old_id = driver.id
    await drivers_service.fire_driver(session, driver)

    new_car = await cars_service.create_car(
        session, plate="BB", model=None, photo_file_id=None, photo_path=None
    )
    again = await drivers_service.register_driver(
        session, tg_user_id=555, full_name="Иванов Иван", phone="+996701",
        inn="12345678", selfie_file_id="s2", selfie_path="p2", car_id=new_car.id,
    )

    assert again.id == old_id, "должны переиспользовать запись, а не создавать вторую"
    assert again.active is True and again.fired_at is None
    assert again.car_id == new_car.id
    # прошлые платежи никуда не делись
    stats = await drivers_service.driver_stats(session, again.id)
    assert stats.total_paid == 500.0
    assert await drivers_service.get_driver_by_tg(session, 555) is not None
