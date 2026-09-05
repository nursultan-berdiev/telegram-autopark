"""Тесты штрafov: домен (app/domain/fines.py) + HTTP (app/routers/fines.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db.models import FineStatus
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import fines as fines_service
from app.main import app
from app.routers import fines as fines_router

UTC = timezone.utc

# Оркестратор подключает роутер в app/main.py отдельно; для автономности тестов
# этого модуля подключаем его сюда же, если ещё не подключён (без дублей).
if not any(getattr(r, "path", None) == "/cars/{car_id}/fines" for r in app.routes):
    app.include_router(fines_router.router, tags=["fines"])


async def _car(session, plate="AA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


async def _driver(session, car_id, tg_id=1):
    return await drivers_service.register_driver(
        session, tg_user_id=tg_id, full_name="Водитель", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car_id,
    )


# --- домен -----------------------------------------------------------------


async def test_add_fine_defaults_issued_at_and_substitutes_current_driver(session):
    car = await _car(session)
    driver = await _driver(session, car.id)

    fine = await fines_service.add_fine(session, car.id, amount=Decimal("500.00"), created_by=111)

    assert fine.driver_id == driver.id  # водитель не передан — подставлен текущий
    assert fine.status is FineStatus.unpaid
    assert fine.issued_at is not None
    assert (datetime.now(UTC) - fine.issued_at.replace(tzinfo=UTC)).total_seconds() < 5


async def test_add_fine_no_driver_on_car_leaves_driver_id_none(session):
    car = await _car(session)
    fine = await fines_service.add_fine(session, car.id, amount=Decimal("100"), created_by=111)
    assert fine.driver_id is None


async def test_add_fine_explicit_driver_not_overridden(session):
    car = await _car(session)
    driver = await _driver(session, car.id)
    # Второй водитель на другой машине: в одной машине двух действующих
    # водителей БД теперь не допускает (uq_driver_active_car).
    other_car = await _car(session, "ZZ")
    other = await drivers_service.register_driver(
        session, tg_user_id=2, full_name="Другой", phone="+1", inn="2",
        selfie_file_id=None, selfie_path=None, car_id=other_car.id,
    )
    fine = await fines_service.add_fine(
        session, car.id, driver_id=other.id, amount=Decimal("1"), created_by=111
    )
    assert fine.driver_id == other.id
    assert driver.id  # используется только для наглядности сетапа


async def test_list_fines(session):
    car = await _car(session)
    await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)
    await fines_service.add_fine(session, car.id, amount=Decimal("2"), created_by=111)
    other_car = await _car(session, "BB")
    await fines_service.add_fine(session, other_car.id, amount=Decimal("3"), created_by=111)

    fines = await fines_service.list_fines(session, car.id)
    assert len(fines) == 2
    assert {f.car_id for f in fines} == {car.id}


async def test_pay_fine_sets_status_and_paid_at(session):
    car = await _car(session)
    fine = await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)

    paid = await fines_service.pay_fine(session, fine.id)
    assert paid is not None
    assert paid.status is FineStatus.paid
    assert paid.paid_at is not None


async def test_pay_fine_not_found_returns_none(session):
    assert await fines_service.pay_fine(session, 999) is None


async def test_delete_fine(session):
    car = await _car(session)
    fine = await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)

    assert await fines_service.delete_fine(session, fine.id) is True
    assert await fines_service.get_fine(session, fine.id) is None


async def test_delete_fine_not_found_returns_false(session):
    assert await fines_service.delete_fine(session, 999) is False


async def test_count_unpaid_excludes_paid(session):
    car = await _car(session)
    a = await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)
    await fines_service.add_fine(session, car.id, amount=Decimal("2"), created_by=111)
    await fines_service.pay_fine(session, a.id)

    assert await fines_service.count_unpaid(session, car.id) == 1


async def test_count_unpaid_window(session):
    car = await _car(session)
    now = datetime.now(UTC)
    await fines_service.add_fine(
        session, car.id, amount=Decimal("1"), created_by=111, issued_at=now - timedelta(days=1)
    )
    await fines_service.add_fine(
        session, car.id, amount=Decimal("2"), created_by=111, issued_at=now - timedelta(days=40)
    )

    assert await fines_service.count_unpaid(session, car.id) == 2  # без окна — все
    assert await fines_service.count_unpaid(session, car.id, window_days=30) == 1


# --- HTTP --------------------------------------------------------------------


async def test_post_fine_as_admin(session, client, admin_headers):
    car = await _car(session)
    resp = await client.post(
        f"/cars/{car.id}/fines",
        json={"amount": "1500.00", "currency": "KGS", "note": "превышение"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["car_id"] == car.id
    assert body["amount"] == "1500.00"
    assert body["status"] == "unpaid"


async def test_post_fine_forbidden_without_admin(session, client):
    car = await _car(session)
    resp = await client.post(f"/cars/{car.id}/fines", json={"amount": "1"})
    assert resp.status_code == 403


async def test_post_fine_forbidden_for_non_admin_tg_id(session, client, admin_headers):
    car = await _car(session)
    headers = dict(admin_headers)
    headers["X-TG-User-Id"] = "999"  # не в ADMIN_IDS
    resp = await client.post(f"/cars/{car.id}/fines", json={"amount": "1"}, headers=headers)
    assert resp.status_code == 403


async def test_post_fine_car_not_found(client, admin_headers):
    resp = await client.post("/cars/999/fines", json={"amount": "1"}, headers=admin_headers)
    assert resp.status_code == 404


async def test_get_fines_list(session, client):
    car = await _car(session)
    await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)
    resp = await client.get(f"/cars/{car.id}/fines")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_get_fines_car_not_found(client):
    resp = await client.get("/cars/999/fines")
    assert resp.status_code == 404


async def test_pay_fine_http(session, client, admin_headers):
    car = await _car(session)
    fine = await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)
    resp = await client.post(f"/fines/{fine.id}/pay", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "paid"
    assert body["paid_at"] is not None


async def test_pay_fine_http_not_found(client, admin_headers):
    resp = await client.post("/fines/999/pay", headers=admin_headers)
    assert resp.status_code == 404


async def test_pay_fine_http_forbidden(session, client):
    car = await _car(session)
    fine = await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)
    resp = await client.post(f"/fines/{fine.id}/pay")
    assert resp.status_code == 403


async def test_delete_fine_http(session, client, admin_headers):
    car = await _car(session)
    fine = await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)
    fine_id = fine.id  # до expire_all: атрибут потом лениво не подгрузить синхронно
    resp = await client.delete(f"/fines/{fine_id}", headers=admin_headers)
    assert resp.status_code == 204
    session.expire_all()  # запрос ушёл через другую AsyncSession — сбрасываем identity map
    assert await fines_service.get_fine(session, fine_id) is None


async def test_delete_fine_http_not_found(client, admin_headers):
    resp = await client.delete("/fines/999", headers=admin_headers)
    assert resp.status_code == 404


async def test_delete_fine_http_forbidden(session, client):
    car = await _car(session)
    fine = await fines_service.add_fine(session, car.id, amount=Decimal("1"), created_by=111)
    resp = await client.delete(f"/fines/{fine.id}")
    assert resp.status_code == 403
