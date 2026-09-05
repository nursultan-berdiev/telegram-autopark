"""Тесты ТО: домен (app/domain/maintenance.py) + HTTP (app/routers/maintenance.py)."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from app.db.models import CarState
from app.domain import cars as cars_service
from app.domain import maintenance as maint_service
from app.main import app
from app.routers import maintenance as maintenance_router

UTC = timezone.utc

# Оркестратор подключает роутер в app/main.py отдельно; для автономности тестов
# этого модуля подключаем его сюда же, если ещё не подключён (без дублей).
if not any(getattr(r, "path", None) == "/cars/{car_id}/maintenance" for r in app.routes):
    app.include_router(maintenance_router.router, tags=["maintenance"])


async def _car(session, plate="AA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


async def _car_state(session, car_id, *, odometer_km=None, tracker_id=None, trusted=True):
    # upsert_item/mark_done сами заводят car_state при отсутствии — переиспользуем строку,
    # иначе повторная вставка упадёт на UNIQUE(car_id).
    state = await session.get(CarState, car_id)
    if state is None:
        state = CarState(car_id=car_id)
        session.add(state)
    state.odometer_km = odometer_km
    state.odometer_tracker_id = tracker_id
    state.odometer_trusted = trusted
    await session.commit()
    await session.refresh(state)
    return state


# --- домен -----------------------------------------------------------------


async def test_upsert_takes_base_and_tracker_from_car_state(session):
    car = await _car(session)
    await _car_state(session, car.id, odometer_km=Decimal("12345.000"), tracker_id=7)

    item = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), created_by=111
    )

    assert item.last_service_km == Decimal("12345.000")
    assert item.last_service_tracker_id == 7


async def test_upsert_explicit_last_service_km_still_gets_tracker_id_from_state(session):
    """last_service_tracker_id ВСЕГДА из car_state — база пробега привязана к трекеру."""
    car = await _car(session)
    await _car_state(session, car.id, odometer_km=Decimal("12345.000"), tracker_id=7)

    item = await maint_service.upsert_item(
        session,
        car.id,
        type="oil",
        interval_km=Decimal("10000"),
        last_service_km=Decimal("5000"),
        created_by=111,
    )

    assert item.last_service_km == Decimal("5000")
    assert item.last_service_tracker_id == 7


async def test_upsert_without_car_state_defaults_to_zero(session):
    car = await _car(session)
    item = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), created_by=111
    )
    assert item.last_service_km == Decimal("0")
    assert item.last_service_tracker_id is None


async def test_upsert_is_idempotent_per_car_and_type(session):
    car = await _car(session)
    first = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), created_by=111
    )
    second = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("12000"), created_by=111
    )
    assert first.id == second.id
    items = await maint_service.list_items(session, car.id)
    assert len(items) == 1
    assert items[0].interval_km == Decimal("12000")


async def test_mark_done_moves_base_to_current_odometer_and_restores_trust(session):
    car = await _car(session)
    await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), last_service_km=Decimal("0"),
        created_by=111,
    )
    state = await _car_state(
        session, car.id, odometer_km=Decimal("20000.000"), tracker_id=3, trusted=False
    )

    item = await maint_service.mark_done(session, car.id, "oil")

    assert item is not None
    assert item.last_service_km == Decimal("20000.000")
    assert item.last_service_tracker_id == 3
    assert item.last_service_at is not None
    assert state.odometer_trusted is True  # доверие к одометру возвращено


async def test_mark_done_without_car_state_defaults_to_zero(session):
    car = await _car(session)
    await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), created_by=111
    )
    item = await maint_service.mark_done(session, car.id, "oil")
    assert item.last_service_km == Decimal("0")

    state = await session.get(CarState, car.id)
    assert state is not None
    assert state.odometer_trusted is True


async def test_mark_done_not_found_returns_none(session):
    car = await _car(session)
    assert await maint_service.mark_done(session, car.id, "unknown") is None


# --- over_km -----------------------------------------------------------------


async def test_over_km_positive(session):
    car = await _car(session)
    item = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("500"), last_service_km=Decimal("1000"),
        created_by=111,
    )
    state = CarState(car_id=car.id, odometer_km=Decimal("1600"))
    assert maint_service.over_km(item, state) == Decimal("100")


async def test_over_km_exact_boundary_is_zero(session):
    car = await _car(session)
    item = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("500"), last_service_km=Decimal("1000"),
        created_by=111,
    )
    state = CarState(car_id=car.id, odometer_km=Decimal("1500"))
    assert maint_service.over_km(item, state) == Decimal("0")


async def test_over_km_not_yet_due_is_negative(session):
    car = await _car(session)
    item = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("500"), last_service_km=Decimal("1000"),
        created_by=111,
    )
    state = CarState(car_id=car.id, odometer_km=Decimal("1499"))
    assert maint_service.over_km(item, state) == Decimal("-1")


async def test_over_km_no_car_state_is_none(session):
    car = await _car(session)
    item = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("500"), created_by=111
    )
    assert maint_service.over_km(item, None) is None


async def test_over_km_car_state_odometer_none_is_none(session):
    car = await _car(session)
    item = await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("500"), created_by=111
    )
    state = CarState(car_id=car.id, odometer_km=None)
    assert maint_service.over_km(item, state) is None


# --- HTTP --------------------------------------------------------------------


async def test_put_maintenance_as_admin(session, client, admin_headers):
    car = await _car(session)
    await _car_state(session, car.id, odometer_km=Decimal("5000"), tracker_id=9)

    resp = await client.put(
        f"/cars/{car.id}/maintenance",
        json={"type": "oil", "interval_km": "10000"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "oil"
    assert body["last_service_km"] == "5000.000" or Decimal(body["last_service_km"]) == 5000
    assert body["last_service_tracker_id"] == 9
    assert body["over_km"] is not None


async def test_put_maintenance_forbidden_without_admin(session, client):
    car = await _car(session)
    resp = await client.put(
        f"/cars/{car.id}/maintenance", json={"type": "oil", "interval_km": "10000"}
    )
    assert resp.status_code == 403


async def test_put_maintenance_car_not_found(client, admin_headers):
    resp = await client.put(
        "/cars/999/maintenance", json={"type": "oil", "interval_km": "10000"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


async def test_get_maintenance_list(session, client):
    car = await _car(session)
    await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), created_by=111
    )
    resp = await client.get(f"/cars/{car.id}/maintenance")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_maintenance_car_not_found(client):
    resp = await client.get("/cars/999/maintenance")
    assert resp.status_code == 404


async def test_mark_done_http(session, client, admin_headers):
    car = await _car(session)
    car_id = car.id  # до expire_all: атрибут потом лениво не подгрузить синхронно
    await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), last_service_km=Decimal("0"),
        created_by=111,
    )
    await _car_state(session, car.id, odometer_km=Decimal("15000"), tracker_id=1, trusted=False)

    resp = await client.post(f"/cars/{car.id}/maintenance/oil/done", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["last_service_km"]) == Decimal("15000")
    # база сброшена на текущий одометр -> "запас" до следующего ТО = -interval_km
    assert Decimal(body["over_km"]) == Decimal("-10000")

    session.expire_all()  # запрос ушёл через другую AsyncSession — сбрасываем identity map
    state = await session.get(CarState, car_id)
    assert state.odometer_trusted is True


async def test_mark_done_http_not_found(session, client, admin_headers):
    car = await _car(session)
    resp = await client.post(f"/cars/{car.id}/maintenance/unknown/done", headers=admin_headers)
    assert resp.status_code == 404


async def test_mark_done_http_car_not_found(client, admin_headers):
    resp = await client.post("/cars/999/maintenance/oil/done", headers=admin_headers)
    assert resp.status_code == 404


async def test_mark_done_http_forbidden_without_admin(session, client):
    car = await _car(session)
    await maint_service.upsert_item(
        session, car.id, type="oil", interval_km=Decimal("10000"), created_by=111
    )
    resp = await client.post(f"/cars/{car.id}/maintenance/oil/done")
    assert resp.status_code == 403
