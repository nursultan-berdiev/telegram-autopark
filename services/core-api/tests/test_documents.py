"""Документы: машины и общие парка, загрузка и удаление только админом."""
from __future__ import annotations

import pytest

from app.db.models import Car
from app.domain import documents
from app.errors import NotFound, Validation
from contracts import DOCUMENT_MAX_FILE_ID, DOCUMENT_MAX_FILE_NAME, DOCUMENT_MAX_TITLE

ADMIN_ID = 111


async def _car(session, plate: str = "01KG001AAA") -> Car:
    car = Car(plate=plate)
    session.add(car)
    await session.commit()
    return car


def _payload(**over) -> dict:
    body = {
        "car_id": None,
        "title": "ОСАГО до 31.12.2026",
        "file_id": "BQACAgIAAxkBAAI",
        "file_kind": "document",
        "file_name": "osago.pdf",
        "file_size": 245_000,
    }
    body.update(over)
    return body


async def _add(session, **over):
    data = _payload(**over)
    return await documents.add_document(session, created_by=ADMIN_ID, **data)


# --- домен ------------------------------------------------------------------


async def test_car_and_common_documents_are_separate(session):
    car = await _car(session)
    other = await _car(session, "01KG002BBB")
    osago = await _add(session, car_id=car.id)
    await _add(session, car_id=other.id, title="ОСАГО другой")
    rules = await _add(session, car_id=None, title="Договор аренды")

    assert [d.id for d in await documents.list_documents(session, car_id=car.id)] == [osago.id]
    assert [d.id for d in await documents.list_documents(session, car_id=None)] == [rules.id]


async def test_newest_document_first(session):
    car = await _car(session)
    old = await _add(session, car_id=car.id, title="ОСАГО 2025")
    new = await _add(session, car_id=car.id, title="ОСАГО 2026")

    assert [d.id for d in await documents.list_documents(session, car_id=car.id)] == [
        new.id, old.id,
    ]


async def test_title_trimmed_and_validated(session):
    doc = await _add(session, title="  Техпаспорт  ")
    assert doc.title == "Техпаспорт" and doc.created_by == ADMIN_ID

    for bad in ({"title": "   "}, {"title": "я" * (DOCUMENT_MAX_TITLE + 1)}):
        with pytest.raises(Validation):
            await _add(session, **bad)


async def test_unknown_car_rejected(session):
    with pytest.raises(NotFound):
        await _add(session, car_id=999)


async def test_delete(session):
    doc = await _add(session)
    assert await documents.delete_document(session, doc.id) is True
    assert await documents.get_document(session, doc.id) is None
    assert await documents.delete_document(session, doc.id) is False


# --- API --------------------------------------------------------------------


async def test_api_add_list_get(admin_client, session):
    car = await _car(session)

    created = await admin_client.post("/documents", json=_payload(car_id=car.id))
    assert created.status_code == 201
    body = created.json()
    assert body["car_id"] == car.id and body["file_kind"] == "document"
    assert body["created_by"] == ADMIN_ID and body["file_size"] == 245_000

    common = await admin_client.post(
        "/documents", json=_payload(title="Правила парка", file_kind="photo", file_name=None)
    )
    assert common.status_code == 201 and common.json()["car_id"] is None

    by_car = await admin_client.get(f"/cars/{car.id}/documents")
    assert [d["id"] for d in by_car.json()] == [body["id"]]
    shared = await admin_client.get("/documents/common")
    assert [d["title"] for d in shared.json()] == ["Правила парка"]
    one = await admin_client.get(f"/documents/{body['id']}")
    assert one.json()["file_id"] == "BQACAgIAAxkBAAI"


async def test_api_upload_and_delete_require_admin(client, admin_client, session):
    doc = (await admin_client.post("/documents", json=_payload())).json()

    as_driver = {"X-TG-User-Id": "501"}
    assert (await client.post("/documents", json=_payload(), headers=as_driver)).status_code == 403
    assert (
        await client.delete(f"/documents/{doc['id']}", headers=as_driver)
    ).status_code == 403
    assert (await admin_client.get("/documents/common")).json()[0]["id"] == doc["id"]


async def test_api_errors(admin_client):
    assert (await admin_client.get("/cars/999/documents")).status_code == 404
    assert (await admin_client.get("/documents/999")).status_code == 404
    assert (await admin_client.delete("/documents/999")).status_code == 404
    assert (await admin_client.post("/documents", json=_payload(car_id=999))).status_code == 404
    assert (await admin_client.post("/documents", json=_payload(title=" "))).status_code == 422


async def test_api_rejects_file_fields_wider_than_columns(admin_client):
    """На Postgres лишняя длина дала бы DataError и 500 — отсекаем контрактом с 422."""
    for bad in (
        {"file_kind": "video"},
        {"file_id": ""},
        {"file_id": "x" * (DOCUMENT_MAX_FILE_ID + 1)},
        {"file_name": "x" * (DOCUMENT_MAX_FILE_NAME + 1)},
        {"file_size": -1},
    ):
        response = await admin_client.post("/documents", json=_payload(**bad))
        assert response.status_code == 422, bad
    assert (await admin_client.get("/documents/common")).json() == []


async def test_api_delete(admin_client):
    doc = (await admin_client.post("/documents", json=_payload())).json()

    assert (await admin_client.delete(f"/documents/{doc['id']}")).status_code == 204
    assert (await admin_client.get(f"/documents/{doc['id']}")).status_code == 404
    assert (await admin_client.get("/documents/common")).json() == []
