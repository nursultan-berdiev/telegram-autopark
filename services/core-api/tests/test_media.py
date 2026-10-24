"""Медиа-прокси веб-админки: резолв file_id из БД и отдача байтов через бота."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.clients.bot_media import BotMediaError
from app.db.models import Car, Document, Driver, PhotoInspection
from app.web.session import COOKIE_NAME, issue

ADMIN = 111
NO_AUTH = {"Authorization": ""}


@pytest.fixture
def fetch(monkeypatch):
    mock = AsyncMock(return_value=(b"BYTES", "image/jpeg"))
    monkeypatch.setattr("app.routers.media.fetch_media", mock)
    return mock


async def _car(session, **over) -> Car:
    car = Car(plate=over.pop("plate", "01KG001AAA"), **over)
    session.add(car)
    await session.commit()
    return car


async def _driver(session, **over) -> Driver:
    driver = Driver(
        tg_user_id=over.pop("tg_user_id", 555),
        full_name="Иванов И.И.",
        phone="+996700000000",
        inn="12345678901234",
        **over,
    )
    session.add(driver)
    await session.commit()
    return driver


async def test_car_photo_returns_bytes(client, session, fetch):
    car = await _car(session, photo_file_id="FID_CAR")
    resp = await client.get(f"/media/car-photo/{car.id}")
    assert resp.status_code == 200
    assert resp.content == b"BYTES"
    assert resp.headers["content-type"] == "image/jpeg"
    fetch.assert_awaited_once_with("FID_CAR")


async def test_car_without_photo_404(client, session, fetch):
    car = await _car(session)
    resp = await client.get(f"/media/car-photo/{car.id}")
    assert resp.status_code == 404
    fetch.assert_not_awaited()


async def test_missing_car_404(client, fetch):
    resp = await client.get("/media/car-photo/9999")
    assert resp.status_code == 404


async def test_driver_selfie(client, session, fetch):
    driver = await _driver(session, selfie_file_id="FID_SELFIE")
    resp = await client.get(f"/media/driver-selfie/{driver.id}")
    assert resp.status_code == 200
    fetch.assert_awaited_once_with("FID_SELFIE")


async def test_document(client, session, fetch):
    doc = Document(car_id=None, title="ОСАГО", file_id="FID_DOC", file_kind="document", created_by=ADMIN)
    session.add(doc)
    await session.commit()
    resp = await client.get(f"/media/document/{doc.id}")
    assert resp.status_code == 200
    fetch.assert_awaited_once_with("FID_DOC")


async def test_inspection_angle(client, session, fetch):
    driver = await _driver(session)
    insp = PhotoInspection(
        driver_id=driver.id,
        front_file_id="F_FRONT",
        rear_file_id="F_REAR",
        interior_front_file_id="F_IF",
        interior_rear_file_id="F_IR",
    )
    session.add(insp)
    await session.commit()
    resp = await client.get(f"/media/inspection/{insp.id}?angle=front")
    assert resp.status_code == 200
    fetch.assert_awaited_once_with("F_FRONT")


async def test_inspection_bad_angle_400(client, session, fetch):
    driver = await _driver(session)
    insp = PhotoInspection(
        driver_id=driver.id,
        front_file_id="F_FRONT",
        rear_file_id="F_REAR",
        interior_front_file_id="F_IF",
        interior_rear_file_id="F_IR",
    )
    session.add(insp)
    await session.commit()
    resp = await client.get(f"/media/inspection/{insp.id}?angle=roof")
    assert resp.status_code == 400


async def test_unknown_kind_404(client, fetch):
    resp = await client.get("/media/wat/1")
    assert resp.status_code == 404


async def test_bot_media_error_502_hides_internal_detail(client, session, monkeypatch):
    car = await _car(session, photo_file_id="FID")
    monkeypatch.setattr(
        "app.routers.media.fetch_media",
        AsyncMock(side_effect=BotMediaError("медиа-сервер бота недоступен: http://bot:8081")),
    )
    resp = await client.get(f"/media/car-photo/{car.id}")
    assert resp.status_code == 502
    assert "bot:8081" not in resp.text  # внутренний адрес клиенту не утекает


async def test_cookie_admin_can_read_media(client, session, fetch):
    car = await _car(session, photo_file_id="FID")
    headers = {"Authorization": "", "Cookie": f"{COOKIE_NAME}={issue(ADMIN)}"}
    resp = await client.get(f"/media/car-photo/{car.id}", headers=headers)
    assert resp.status_code == 200


async def test_no_auth_rejected(client, session, fetch):
    car = await _car(session, photo_file_id="FID")
    resp = await client.get(f"/media/car-photo/{car.id}", headers=NO_AUTH)
    assert resp.status_code == 401


# --- защита типов/заголовков (стержень находки XSS) -------------------------


async def test_image_is_inline_with_security_headers(client, session, fetch):
    car = await _car(session, photo_file_id="FID")
    resp = await client.get(f"/media/car-photo/{car.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-disposition"] == "inline"
    assert "sandbox" in resp.headers["content-security-policy"]
    assert resp.headers["cache-control"] == "private, max-age=3600"


async def test_dangerous_type_forced_to_octet_stream_attachment(client, session, monkeypatch):
    """Даже если бот вернул text/html / image/svg+xml — не наш origin и не inline."""
    car = await _car(session, photo_file_id="FID")
    monkeypatch.setattr(
        "app.routers.media.fetch_media", AsyncMock(return_value=(b"<svg/>", "image/svg+xml"))
    )
    resp = await client.get(f"/media/car-photo/{car.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["content-disposition"] == "attachment"
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_pdf_is_attachment(client, session, monkeypatch):
    doc = Document(car_id=None, title="ОСАГО", file_id="FID", file_kind="document", created_by=ADMIN)
    session.add(doc)
    await session.commit()
    monkeypatch.setattr(
        "app.routers.media.fetch_media", AsyncMock(return_value=(b"%PDF", "application/pdf"))
    )
    resp = await client.get(f"/media/document/{doc.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == "attachment"


# --- клиент к медиа-серверу бота (httpx.MockTransport, без мока fetch_media) --


def _client_with(monkeypatch, handler):
    import functools

    import httpx

    monkeypatch.setattr(
        "app.clients.bot_media.httpx.AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )


async def test_fetch_media_builds_url_quotes_id_and_sends_bearer(monkeypatch):
    import httpx

    from app.clients import bot_media
    from app.config import settings

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"IMG", headers={"content-type": "image/png"})

    monkeypatch.setattr(settings, "bot_media_url", "http://bot:8081")
    monkeypatch.setattr(settings, "core_api_token", "tok")
    _client_with(monkeypatch, handler)

    data, ctype = await bot_media.fetch_media("A/b+c")

    assert (data, ctype) == (b"IMG", "image/png")
    assert seen["auth"] == "Bearer tok"
    assert seen["url"] == "http://bot:8081/media/A%2Fb%2Bc"


async def test_fetch_media_404_raises(monkeypatch):
    import httpx

    from app.clients import bot_media

    _client_with(monkeypatch, lambda r: httpx.Response(404))
    with pytest.raises(BotMediaError):
        await bot_media.fetch_media("x")


async def test_fetch_media_5xx_raises(monkeypatch):
    import httpx

    from app.clients import bot_media

    _client_with(monkeypatch, lambda r: httpx.Response(503))
    with pytest.raises(BotMediaError):
        await bot_media.fetch_media("x")


async def test_fetch_media_network_error_raises(monkeypatch):
    import httpx

    from app.clients import bot_media

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    _client_with(monkeypatch, boom)
    with pytest.raises(BotMediaError):
        await bot_media.fetch_media("x")
