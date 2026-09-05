"""HTTP-тесты роутера cars (contracts.CarDTO)."""
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service


async def test_create_and_get_car(admin_client):
    resp = await admin_client.post("/cars", json={"plate": "01a123bc", "model": "Cobalt"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["plate"] == "01A123BC"  # нормализация в верхний регистр — как в боте
    assert body["status"] == "free"
    assert body["driver_id"] is None

    resp = await admin_client.get(f"/cars/{body['id']}")
    assert resp.status_code == 200
    assert resp.json()["plate"] == "01A123BC"


async def test_create_duplicate_plate_conflict(admin_client):
    await admin_client.post("/cars", json={"plate": "AA"})
    resp = await admin_client.post("/cars", json={"plate": "aa"})
    assert resp.status_code == 409


async def test_get_car_not_found(admin_client):
    resp = await admin_client.get("/cars/999")
    assert resp.status_code == 404


async def test_list_cars_free_filter_and_driver_fields(session, admin_client):
    free_car = await cars_service.create_car(
        session, plate="FREE1", model=None, photo_file_id=None, photo_path=None
    )
    occupied_car = await cars_service.create_car(
        session, plate="OCC1", model=None, photo_file_id=None, photo_path=None
    )
    await drivers_service.register_driver(
        session, tg_user_id=500, full_name="Иванов", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=occupied_car.id,
    )

    resp = await admin_client.get("/cars")
    assert resp.status_code == 200
    cars = resp.json()
    assert {c["plate"] for c in cars} == {"FREE1", "OCC1"}
    occ = next(c for c in cars if c["plate"] == "OCC1")
    assert occ["driver_id"] is not None
    assert occ["driver_name"] == "Иванов"
    free = next(c for c in cars if c["plate"] == "FREE1")
    assert free["driver_id"] is None

    resp = await admin_client.get("/cars", params={"free": 1})
    assert {c["plate"] for c in resp.json()} == {"FREE1"}
    assert free_car.id  # используется только для наглядности сетапа


async def test_delete_free_car(session, admin_client):
    car = await cars_service.create_car(
        session, plate="DEL1", model=None, photo_file_id=None, photo_path=None
    )
    car_id = car.id  # до expire_all: атрибут потом лениво не подгрузить синхронно
    resp = await admin_client.delete(f"/cars/{car_id}")
    assert resp.status_code == 204
    session.expire_all()  # запрос ушёл через другую AsyncSession — сбрасываем identity map
    assert await cars_service.get_car(session, car_id) is None


async def test_delete_occupied_car_conflict(session, admin_client):
    car = await cars_service.create_car(
        session, plate="OCC2", model=None, photo_file_id=None, photo_path=None
    )
    await drivers_service.register_driver(
        session, tg_user_id=501, full_name="Петров", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    resp = await admin_client.delete(f"/cars/{car.id}")
    assert resp.status_code == 409


async def test_delete_car_not_found(admin_client):
    resp = await admin_client.delete("/cars/999")
    assert resp.status_code == 404
