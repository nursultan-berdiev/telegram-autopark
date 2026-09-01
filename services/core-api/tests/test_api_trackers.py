"""Привязка трекера и последствия его смены для базы пробега."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import (
    Car,
    CarState,
    Fine,
    MaintenanceItem,
    Tracker,
    TrackerProvider,
)


async def _car(session, plate="01KG606AAA"):
    car = Car(plate=plate)
    session.add(car)
    await session.commit()
    return car


async def test_bind_tracker_and_read_back(admin_client, session):
    car = await _car(session)

    bound = await admin_client.put(
        f"/cars/{car.id}/tracker", json={"external_id": "9175358042"}
    )
    assert bound.status_code == 200
    assert bound.json()["external_id"] == "9175358042"

    read = await admin_client.get(f"/cars/{car.id}/tracker")
    assert read.json()["external_id"] == "9175358042"


async def test_same_device_cannot_serve_two_cars(admin_client, session):
    first = await _car(session, plate="01KG601AAA")
    second = await _car(session, plate="01KG602AAA")

    await admin_client.put(f"/cars/{first.id}/tracker", json={"external_id": "9175358042"})
    conflict = await admin_client.put(
        f"/cars/{second.id}/tracker", json={"external_id": "9175358042"}
    )

    assert conflict.status_code == 409


async def test_unknown_provider_is_422(admin_client, session):
    car = await _car(session)

    response = await admin_client.put(
        f"/cars/{car.id}/tracker", json={"provider": "глонасс-3000", "external_id": "1"}
    )

    assert response.status_code == 422


async def test_tracker_change_does_not_zero_maintenance_base(admin_client, session):
    """Обнулять базу ТО в ноль нельзя: первая же точка дала бы ложный алерт."""
    car = await _car(session)
    car_id = car.id
    old = Tracker(car_id=car_id, provider=TrackerProvider.traccar, external_id="1111111111")
    session.add(old)
    await session.flush()
    session.add(
        MaintenanceItem(
            car_id=car_id,
            type="oil",
            interval_km=Decimal("10000"),
            last_service_km=Decimal("42000"),
            last_service_tracker_id=old.id,
        )
    )
    await session.commit()

    await admin_client.put(f"/cars/{car_id}/tracker", json={"external_id": "2222222222"})

    session.expire_all()
    item = await session.scalar(select(MaintenanceItem).where(MaintenanceItem.car_id == car_id))
    state = await session.get(CarState, car_id)
    assert Decimal(str(item.last_service_km)) == Decimal("42000"), "старая база сохранена"
    assert state.odometer_trusted is False, "но помечена недостоверной до отметки ТО"


async def test_unbind_keeps_row_for_history(admin_client, session):
    """Строку трекера не удаляем: на неё ссылается телеметрия и аудит команд."""
    car = await _car(session)
    car_id = car.id
    await admin_client.put(f"/cars/{car_id}/tracker", json={"external_id": "9175358042"})

    removed = await admin_client.delete(f"/cars/{car_id}/tracker")
    assert removed.status_code == 204

    session.expire_all()
    tracker = await session.scalar(select(Tracker).where(Tracker.car_id == car_id))
    assert tracker is not None, "строка осталась"
    assert tracker.active is False, "но деактивирована"
    assert (await admin_client.get(f"/cars/{car_id}/tracker")).json() is None


async def test_car_with_history_is_not_deleted_silently(admin_client, session):
    car = await _car(session)
    session.add(Fine(car_id=car.id, issued_at=datetime.now(timezone.utc)))
    await session.commit()

    response = await admin_client.delete(f"/cars/{car.id}")

    assert response.status_code == 409
    assert "история" in response.json()["detail"]


async def test_one_active_driver_per_car(session):
    """Гонка двух регистраций по одному инвайту не должна посадить двоих в машину."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.db.models import Driver

    car = await _car(session, plate="01KG777BBB")
    session.add(
        Driver(tg_user_id=1, full_name="Первый", phone="+1", inn="1", car_id=car.id, active=True)
    )
    await session.commit()

    session.add(
        Driver(tg_user_id=2, full_name="Второй", phone="+2", inn="2", car_id=car.id, active=True)
    )
    with pytest.raises(IntegrityError):
        await session.commit()
