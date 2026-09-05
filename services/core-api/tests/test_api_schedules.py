"""HTTP-тесты роутера schedules (contracts.ScheduleWithStatus)."""
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service


async def _driver(session, *, tg_id=1, plate="AA"):
    car = await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )
    return await drivers_service.register_driver(
        session, tg_user_id=tg_id, full_name="Водитель", phone="+1", inn="11111111",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )


async def test_get_schedule_missing_returns_nulls(session, admin_client):
    driver = await _driver(session)
    resp = await admin_client.get(f"/drivers/{driver.id}/schedule")
    assert resp.status_code == 200
    assert resp.json() == {"schedule": None, "status": None}


async def test_get_schedule_missing_driver_also_returns_nulls(admin_client):
    # GET не проверяет существование водителя — только наличие графика (см. plan/03).
    resp = await admin_client.get("/drivers/999/schedule")
    assert resp.status_code == 200
    assert resp.json() == {"schedule": None, "status": None}


async def test_put_schedule_creates_and_returns_status(session, admin_client):
    driver = await _driver(session, tg_id=2, plate="BB")
    resp = await admin_client.put(
        f"/drivers/{driver.id}/schedule",
        json={
            "period": "weekly",
            "amount": "1500.00",
            "next_due_date": "2027-01-01T00:00:00+00:00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["schedule"]["driver_id"] == driver.id
    assert body["schedule"]["period"] == "weekly"
    assert float(body["schedule"]["amount"]) == 1500.0
    assert body["status"]["period_label"] == "еженедельно"
    assert body["status"]["is_overdue"] is False
    assert float(body["status"]["remaining_current"]) == 1500.0


async def test_get_schedule_reflects_put(session, admin_client):
    driver = await _driver(session, tg_id=3, plate="CC")
    put_resp = await admin_client.put(
        f"/drivers/{driver.id}/schedule",
        json={
            "period": "monthly",
            "amount": "2000.00",
            "next_due_date": "2027-02-01T00:00:00+00:00",
        },
    )
    schedule_id = put_resp.json()["schedule"]["id"]

    get_resp = await admin_client.get(f"/drivers/{driver.id}/schedule")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["schedule"]["id"] == schedule_id
    assert body["schedule"]["amount"] == put_resp.json()["schedule"]["amount"]


async def test_put_schedule_driver_not_found(admin_client):
    resp = await admin_client.put(
        "/drivers/999/schedule",
        json={
            "period": "weekly",
            "amount": "500.00",
            "next_due_date": "2027-01-01T00:00:00+00:00",
        },
    )
    assert resp.status_code == 404


async def test_put_schedule_invalid_period_422(session, admin_client):
    driver = await _driver(session, tg_id=4, plate="DD")
    resp = await admin_client.put(
        f"/drivers/{driver.id}/schedule",
        json={
            "period": "yearly",
            "amount": "500.00",
            "next_due_date": "2027-01-01T00:00:00+00:00",
        },
    )
    assert resp.status_code == 422


async def test_put_schedule_custom_without_interval_422(session, admin_client):
    driver = await _driver(session, tg_id=5, plate="EE")
    resp = await admin_client.put(
        f"/drivers/{driver.id}/schedule",
        json={
            "period": "custom",
            "amount": "500.00",
            "next_due_date": "2027-01-01T00:00:00+00:00",
        },
    )
    assert resp.status_code == 422


async def test_put_schedule_custom_with_interval_ok(session, admin_client):
    driver = await _driver(session, tg_id=6, plate="FF")
    resp = await admin_client.put(
        f"/drivers/{driver.id}/schedule",
        json={
            "period": "custom",
            "interval_days": 10,
            "amount": "500.00",
            "next_due_date": "2027-01-01T00:00:00+00:00",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["schedule"]["interval_days"] == 10
    assert body["status"]["period_label"] == "каждые 10 дн."


async def test_get_schedule_overdue_status(session, admin_client):
    driver = await _driver(session, tg_id=7, plate="GG")
    await admin_client.put(
        f"/drivers/{driver.id}/schedule",
        json={
            "period": "monthly",
            "amount": "1000.00",
            "next_due_date": "2020-01-01T00:00:00+00:00",
        },
    )
    resp = await admin_client.get(f"/drivers/{driver.id}/schedule")
    body = resp.json()
    assert body["status"]["is_overdue"] is True
    assert body["status"]["overdue_periods"] >= 1
    assert "просрочен" in body["status"]["summary"]
