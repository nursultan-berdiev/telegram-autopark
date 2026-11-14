"""Главная админки и страница машины: обзор парка, оплаты и осмотры по машине."""
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from app.db.models import (
    Alert,
    AlertStatus,
    AlertType,
    Car,
    CarState,
    Command,
    CommandStatus,
    CommandType,
    Driver,
    Fine,
    FineStatus,
    Payment,
    PhotoInspection,
    PhotoSchedule,
    SchedulePeriod,
)
from app.domain import alerts as alerts_domain
from app.domain import drivers as drivers_domain
from app.domain import photo_inspections as photo_domain
from app.domain import schedules as sched

_TG_IDS = count(1000)  # hash(plate) зависел бы от PYTHONHASHSEED


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _car(session, plate, *, driver=None, photo=None):
    car = Car(plate=plate, model="Honda Stream", photo_file_id=photo)
    session.add(car)
    await session.flush()
    drv = None
    if driver:
        drv = Driver(tg_user_id=next(_TG_IDS), full_name=driver, phone="+1", inn=plate,
                     car_id=car.id, active=True)
        session.add(drv)
        await session.flush()
    return car, drv


def _inspection(driver_id, car_id, created_at):
    return PhotoInspection(
        driver_id=driver_id, car_id=car_id, front_file_id="F", rear_file_id="R",
        interior_front_file_id="IF", interior_rear_file_id="IR", created_at=created_at,
    )


async def test_overview_card_collects_everything_about_car(session, client):
    now = _now()
    car, drv = await _car(session, "01KG195API", driver="Иванов", photo="tg-photo")
    free, _ = await _car(session, "01KG999API")
    # Последняя — по дате из чека, а не по порядку приёма: чек за прошлую неделю,
    # присланный позже, не должен стать «последней оплатой».
    session.add(Payment(driver_id=drv.id, car_id=car.id, amount=1500,
                        paid_at=now - timedelta(hours=1)))
    await session.flush()
    session.add(Payment(driver_id=drv.id, car_id=car.id, amount=900,
                        paid_at=now - timedelta(days=5)))
    session.add(CarState(car_id=car.id, last_ts=now, engine_blocked=False))
    session.add(Command(car_id=car.id, type=CommandType.engine_stop, status=CommandStatus.armed,
                        source="overdue", created_at=now, updated_at=now))
    due = photo_domain.local_today(now) - timedelta(days=2)
    session.add(PhotoSchedule(driver_id=drv.id, next_due_on=due))
    session.add(_inspection(drv.id, car.id, now - timedelta(days=9)))
    session.add(Fine(car_id=car.id, issued_at=now, status=FineStatus.unpaid, external_ref="u1"))
    session.add(Fine(car_id=car.id, issued_at=now, status=FineStatus.unpaid, external_ref="u2"))
    session.add(Fine(car_id=car.id, issued_at=now, status=FineStatus.paid, external_ref="p1"))
    await alerts_domain.raise_alert(
        session, car_id=car.id, atype=AlertType.overdue_payment, payload={}, text="просрочка"
    )
    session.add(Alert(car_id=car.id, type=AlertType.new_fine, status=AlertStatus.resolved,
                      payload={}))
    await sched.set_schedule(
        session, driver_id=drv.id, period=SchedulePeriod.daily, interval_days=None,
        amount=1800.0, next_due_date=now - timedelta(days=3),
    )
    await session.commit()

    resp = await client.get("/cars/overview")
    assert resp.status_code == 200
    cards = {c["plate"]: c for c in resp.json()}

    c = cards["01KG195API"]
    assert c["has_photo"] is True and c["driver_name"] == "Иванов" and c["model"] == "Honda Stream"
    assert float(c["last_payment_amount"]) == 1500
    assert c["next_payment_due"] is not None and c["payment_overdue_days"] >= 3
    assert float(c["payment_debt"]) > 0
    assert c["online"] is True and c["engine_blocked"] is False
    assert c["block_status"] == "armed" and c["block_source"] == "overdue"
    assert c["last_inspection_at"] is not None
    assert c["inspection_overdue_days"] == 2 and c["inspection_days_left"] == -2
    assert c["unpaid_fines"] == 2 and c["open_alerts"] == 1

    f = cards["01KG999API"]
    assert f["driver_id"] is None and f["has_photo"] is False
    assert f["last_payment_at"] is None and f["next_payment_due"] is None
    assert f["inspection_due_on"] is None and f["inspection_days_left"] is None  # водителя нет
    assert f["block_status"] is None and f["online"] is False
    assert f["unpaid_fines"] == 0 and f["open_alerts"] == 0


async def test_overview_empty_fleet_and_auth(client):
    assert (await client.get("/cars/overview")).json() == []
    anon = await client.get("/cars/overview", headers={"Authorization": ""})
    assert anon.status_code == 401


async def test_car_payments_from_any_driver_newest_first(session, client):
    now = _now()
    car, drv = await _car(session, "01KG195API", driver="Иванов")
    other, _ = await _car(session, "01KG999API")
    # Прежний водитель машины уже уволен — его оплаты остаются в истории машины.
    old = Driver(tg_user_id=7, full_name="Петров", phone="+2", inn="2", car_id=None, active=False)
    session.add(old)
    await session.flush()
    session.add(Payment(driver_id=old.id, car_id=car.id, amount=700,
                        paid_at=now - timedelta(days=10)))
    session.add(Payment(driver_id=drv.id, car_id=car.id, amount=1500,
                        paid_at=now - timedelta(days=1)))
    session.add(Payment(driver_id=drv.id, car_id=other.id, amount=999, paid_at=now))
    await session.commit()

    resp = await client.get(f"/cars/{car.id}/payments")
    assert resp.status_code == 200
    rows = resp.json()
    got = [(r["driver_name"], float(r["amount"])) for r in rows]
    assert got == [("Иванов", 1500), ("Петров", 700)]
    assert len((await client.get(f"/cars/{car.id}/payments?limit=1")).json()) == 1
    assert (await client.get("/cars/9999/payments")).status_code == 404


async def test_car_photo_inspections_by_car(session, client):
    now = _now()
    car, drv = await _car(session, "01KG195API", driver="Иванов")
    other, drv2 = await _car(session, "01KG999API", driver="Сидоров")
    session.add(_inspection(drv.id, car.id, now - timedelta(days=7)))
    session.add(_inspection(drv.id, car.id, now - timedelta(days=1)))
    session.add(_inspection(drv2.id, other.id, now))
    await session.commit()

    resp = await client.get(f"/cars/{car.id}/photo-inspections")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2 and all(r["car_plate"] == "01KG195API" for r in rows)
    assert rows[0]["created_at"] > rows[1]["created_at"]  # свежие первыми
    assert (await client.get("/cars/9999/photo-inspections")).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "payments?limit=0", "payments?limit=501",
        "photo-inspections?limit=0", "photo-inspections?limit=201",
    ],
)
async def test_car_history_limit_bounds(session, client, path):
    car, _ = await _car(session, "01KG195API")
    await session.commit()
    assert (await client.get(f"/cars/{car.id}/{path}")).status_code == 422


async def test_active_by_car_skips_fired_and_free(session):
    car, drv = await _car(session, "01KG195API", driver="Иванов")
    free, _ = await _car(session, "01KG999API")
    session.add(Driver(tg_user_id=next(_TG_IDS), full_name="Уволен", phone="+3", inn="3",
                       car_id=free.id, active=False))
    await session.commit()

    active = await drivers_domain.active_by_car(session)
    assert {car_id: d.id for car_id, d in active.items()} == {car.id: drv.id}
    assert list(await drivers_domain.active_by_car(session, [free.id])) == []
    assert await drivers_domain.active_by_car(session, []) == {}


async def test_last_inspection_by_car_counts_any_driver(session):
    now = _now()
    car, drv = await _car(session, "01KG195API", driver="Иванов")
    old = Driver(tg_user_id=next(_TG_IDS), full_name="Прежний", phone="+4", inn="4",
                 car_id=None, active=False)
    session.add(old)
    await session.flush()
    session.add(_inspection(drv.id, car.id, now - timedelta(days=9)))
    session.add(_inspection(old.id, car.id, now - timedelta(days=2)))  # сдал прежний водитель
    session.add(_inspection(drv.id, None, now))  # машину удалили — в счёт не идёт
    await session.commit()

    last = await photo_domain.last_by_car(session)
    assert set(last) == {car.id}
    assert last[car.id].replace(tzinfo=None) == (now - timedelta(days=2)).replace(tzinfo=None)
