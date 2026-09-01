"""HTTP-тесты роутера drivers и /me (роль по tg id)."""
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import invitations as inv_service


async def _car(session, plate="AA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


async def test_list_drivers_active_and_fired(session, client):
    car1 = await _car(session, "AA")
    car2 = await _car(session, "BB")
    await drivers_service.register_driver(
        session, tg_user_id=1, full_name="Активный", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car1.id,
    )
    fired = await drivers_service.register_driver(
        session, tg_user_id=2, full_name="Уволенный", phone="+2", inn="2",
        selfie_file_id=None, selfie_path=None, car_id=car2.id,
    )
    await drivers_service.fire_driver(session, fired)

    resp = await client.get("/drivers")
    assert resp.status_code == 200
    assert {d["full_name"] for d in resp.json()} == {"Активный"}

    resp = await client.get("/drivers", params={"active": 0})
    assert resp.status_code == 200
    assert {d["full_name"] for d in resp.json()} == {"Уволенный"}


async def test_get_driver_with_stats(session, client):
    car = await _car(session)
    driver = await drivers_service.register_driver(
        session, tg_user_id=10, full_name="Иванов", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    resp = await client.get(f"/drivers/{driver.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["driver"]["full_name"] == "Иванов"
    assert body["driver"]["car_plate"] == car.plate
    assert body["stats"]["payments_count"] == 0


async def test_get_driver_not_found(client):
    resp = await client.get("/drivers/999")
    assert resp.status_code == 404


async def test_register_via_invite(session, client):
    car = await _car(session, "REG1")
    invite = await inv_service.create_invitation(session, car_id=car.id, created_by=111)
    car_id, code = car.id, invite.code  # до expire_all — иначе ленивая подгрузка упадёт

    resp = await client.post(
        "/drivers/register",
        json={
            "code": code,
            "tg_user_id": 777,
            "full_name": "Новый Водитель",
            "phone": "+996700000000",
            "inn": "12345678",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["car_id"] == car_id
    assert body["car_plate"] == "REG1"

    # приглашение сожжено, машина занята — запрос ушёл через другую AsyncSession
    session.expire_all()
    check = await inv_service.resolve_invitation(session, code)
    assert check.problem is not None


async def test_register_rejects_unknown_code(client):
    resp = await client.post(
        "/drivers/register",
        json={
            "code": "выдуманный-код",
            "tg_user_id": 778,
            "full_name": "Кто-то",
            "phone": "+996",
            "inn": "1",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "not_found"


async def test_register_rejects_when_car_taken(session, client):
    """Второе приглашение живо, но машину уже занял первый — отказ car_taken (PJ-13)."""
    car = await _car(session, "TAKEN")
    first = await inv_service.create_invitation(session, car_id=car.id, created_by=111)
    second = await inv_service.create_invitation(session, car_id=car.id, created_by=111)

    await drivers_service.register_driver(
        session, tg_user_id=900, full_name="Первый", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    await inv_service.mark_used(session, first, used_by=900)

    resp = await client.post(
        "/drivers/register",
        json={
            "code": second.code,
            "tg_user_id": 901,
            "full_name": "Второй",
            "phone": "+2",
            "inn": "2",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "car_taken"


async def test_fire_driver(session, client, admin_headers):
    car = await _car(session, "FIRE1")
    driver = await drivers_service.register_driver(
        session, tg_user_id=20, full_name="Уволим", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    resp = await client.post(f"/drivers/{driver.id}/fire", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["freed_plate"] == "FIRE1"


async def test_fire_not_found(client, admin_headers):
    resp = await client.post("/drivers/999/fire", headers=admin_headers)
    assert resp.status_code == 404


# --- /me: роль по tg id (admin/driver/guest) --------------------------------


async def test_me_admin(client):
    resp = await client.get("/me", params={"tg_id": 111})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert body["driver"] is None


async def test_me_driver(session, client):
    car = await _car(session, "ME1")
    await drivers_service.register_driver(
        session, tg_user_id=555, full_name="Я водитель", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    resp = await client.get("/me", params={"tg_id": 555})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "driver"
    assert body["driver"]["full_name"] == "Я водитель"
    assert body["driver"]["car_plate"] == "ME1"


async def test_me_guest(client):
    resp = await client.get("/me", params={"tg_id": 999999})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "guest"
    assert body["driver"] is None


async def test_me_fired_driver_becomes_guest(session, client):
    car = await _car(session, "ME2")
    driver = await drivers_service.register_driver(
        session, tg_user_id=556, full_name="Бывший", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    await drivers_service.fire_driver(session, driver)

    resp = await client.get("/me", params={"tg_id": 556})
    assert resp.json()["role"] == "guest"
