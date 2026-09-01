"""HTTP-тесты роутера invitations."""
from app.domain import cars as cars_service


async def _car(session, plate="AA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


async def test_create_invitation(session, client, admin_headers):
    car = await _car(session, "INV1")
    resp = await client.post("/invitations", json={"car_id": car.id}, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["car_id"] == car.id
    assert body["code"]
    assert body["ttl_label"]  # TTL из settings.invite_ttl, не от клиента


async def test_create_invitation_car_not_found(client, admin_headers):
    resp = await client.post("/invitations", json={"car_id": 999}, headers=admin_headers)
    assert resp.status_code == 404


async def test_resolve_ok(session, client, admin_headers):
    car = await _car(session, "INV2")
    created = await client.post("/invitations", json={"car_id": car.id}, headers=admin_headers)
    code = created.json()["code"]

    resp = await client.get("/invitations/resolve", params={"code": code})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["problem"] is None
    assert body["car_id"] == car.id
    assert body["car_plate"] == "INV2"


async def test_resolve_not_found(client, admin_headers):
    resp = await client.get("/invitations/resolve", params={"code": "нет-такого-кода"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["problem"] == "not_found"


async def test_resolve_car_taken(session, client, admin_headers):
    """Живая ссылка на уже занятую машину — problem=car_taken, не «истёк срок»."""
    from app.domain import drivers as drivers_service
    from app.domain import invitations as inv_service

    car = await _car(session, "INV3")
    first = await inv_service.create_invitation(session, car_id=car.id, created_by=111)
    second = await inv_service.create_invitation(session, car_id=car.id, created_by=111)

    await drivers_service.register_driver(
        session, tg_user_id=42, full_name="Занял", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    await inv_service.mark_used(session, first, used_by=42)

    resp = await client.get("/invitations/resolve", params={"code": second.code})
    body = resp.json()
    assert body["ok"] is False
    assert body["problem"] == "car_taken"
