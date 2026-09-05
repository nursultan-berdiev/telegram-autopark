"""Импорт штрафов из внешнего источника: идемпотентность и сопоставление номеров."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import fines as fines_service
import pytest
import pytest_asyncio
from app.db.base import Base
from app.errors import Conflict
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.main import app
from tests.conftest import ADMIN_ID, CORE_TOKEN, IMPORT_TOKEN

UTC = timezone.utc

def test_router_registered_once():
    """Роутер fines подключается в app/main.py — дублировать его здесь нельзя."""
    paths = [getattr(r, "path", None) for r in app.routes]
    assert paths.count("/fines/import") == 1


async def _car(session, plate):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


def _item(plate, ref, **kw):
    return fines_service.FineImportRow(plate=plate, external_ref=ref, **kw)


def _import_headers(token=IMPORT_TOKEN, actor=ADMIN_ID):
    return {"Authorization": f"Bearer {token}", "X-TG-User-Id": str(actor)}


async def test_import_creates_fines_with_source_and_amount(session):
    car = await _car(session, "01KG139API")

    created, skipped, unknown, _ = await fines_service.import_fines(
        session,
        [
            _item(
                "01KG139API",
                "АМ1234567",
                amount=Decimal("3000.00"),
                currency="KGS",
                issued_at=datetime(2026, 8, 17, tzinfo=UTC),
                note="превышение скорости",
            )
        ],
        created_by=ADMIN_ID,
    )

    assert (created, skipped, unknown) == (1, 0, [])
    fines = await fines_service.list_fines(session, car.id)
    assert len(fines) == 1
    assert fines[0].source == "carcheck"
    assert fines[0].external_ref == "АМ1234567"
    assert fines[0].amount == Decimal("3000.00")


async def test_repeated_import_does_not_duplicate(session):
    """Раннер гоняется ежедневно и каждый раз видит те же постановления."""
    car = await _car(session, "01KG139API")
    items = [_item("01KG139API", "АМ1234567", amount=Decimal("3000"))]

    await fines_service.import_fines(session, items)
    created, skipped, _, _ = await fines_service.import_fines(session, items)

    assert (created, skipped) == (0, 1)
    assert len(await fines_service.list_fines(session, car.id)) == 1


async def test_import_matches_plate_ignoring_spaces_and_case(session):
    car = await _car(session, "01KG139API")

    created, _, unknown, _ = await fines_service.import_fines(
        session, [_item("01 kg 139 api", "АМ7654321")]
    )

    assert (created, unknown) == (1, [])
    assert len(await fines_service.list_fines(session, car.id)) == 1


async def test_import_reports_plates_outside_fleet(session):
    await _car(session, "01KG139API")

    created, skipped, unknown, _ = await fines_service.import_fines(
        session, [_item("02KG777XYZ", "АМ0000001")]
    )

    assert (created, skipped) == (0, 0)
    assert unknown == ["02KG777XYZ"]


async def test_unknown_plate_reported_once_across_spellings(session):
    await _car(session, "01KG139API")

    _, _, unknown, _ = await fines_service.import_fines(
        session,
        [_item("02KG777XYZ", "AM1"), _item("02 kg 777 xyz", "AM2")],
    )

    assert len(unknown) == 1, "один чужой номер — одна строка в отчёте"


async def test_ambiguous_plate_is_not_imported_blindly(session):
    """Две записи парка сводятся к одному номеру — угадывать машину нельзя."""
    await _car(session, "01KG139API")
    await _car(session, "01 KG 139 API")

    created, _, unknown, ambiguous = await fines_service.import_fines(
        session, [_item("01KG139API", "АМ1234567")]
    )

    assert created == 0
    assert unknown == []
    assert ambiguous == ["01KG139API"]


async def test_manual_fine_with_duplicate_ref_is_conflict_not_crash(session):
    """Бот берёт номер постановления из примечания — совпадения бывают."""
    car = await _car(session, "01KG139API")
    await fines_service.add_fine(session, car.id, external_ref="п.227")

    with pytest.raises(Conflict):
        await fines_service.add_fine(session, car.id, external_ref="п.227")


@pytest_asyncio.fixture
async def fk_session():
    """SQLite по умолчанию не проверяет внешние ключи и прячет такие падения."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def test_broken_driver_is_not_reported_as_duplicate(fk_session):
    """Битый driver_id — не дубль: подменять причину на 409 значит врать."""
    car = await _car(fk_session, "01KG139API")

    with pytest.raises(IntegrityError):
        await fines_service.add_fine(
            fk_session, car.id, driver_id=10**6, external_ref="AM1"
        )


async def test_import_substitutes_current_driver(session):
    """Штраф без водителя нельзя предъявить — подставляем закреплённого."""
    car = await _car(session, "01KG139API")
    driver = await drivers_service.register_driver(
        session, tg_user_id=1, full_name="Водитель", phone="+1", inn="1",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )

    await fines_service.import_fines(session, [_item("01KG139API", "АМ1234567")])

    fines = await fines_service.list_fines(session, car.id)
    assert fines[0].driver_id == driver.id


async def test_import_survives_partial_failure(session):
    """Один дубль в пачке не должен ронять остальные штрафы."""
    await _car(session, "01KG139API")
    await fines_service.import_fines(session, [_item("01KG139API", "AM1")])

    created, skipped, _, _ = await fines_service.import_fines(
        session,
        [_item("01KG139API", "AM1"), _item("01KG139API", "AM2")],
    )

    assert (created, skipped) == (1, 1)


async def test_import_endpoint_requires_token(client):
    resp = await client.post(
        "/fines/import", json=[{"plate": "01KG139API", "external_ref": "AM1"}]
    )
    assert resp.status_code in (401, 403)


async def test_import_endpoint_rejects_master_token(client):
    """Расширение живёт в браузере: мастер-ключ там открыл бы весь домен."""
    resp = await client.post(
        "/fines/import",
        json=[{"plate": "01KG139API", "external_ref": "AM1"}],
        headers=_import_headers(token=CORE_TOKEN),
    )
    assert resp.status_code == 403


async def test_import_token_does_not_open_the_rest_of_the_api(client):
    resp = await client.get("/cars", headers=_import_headers())
    assert resp.status_code == 401


async def test_import_plates_available_to_narrow_token(client, session):
    await _car(session, "01KG139API")

    resp = await client.get("/fines/import/plates", headers=_import_headers())

    assert resp.status_code == 200
    assert resp.json() == ["01KG139API"]


async def test_import_endpoint_returns_counters(client, session):
    await _car(session, "01KG139API")

    resp = await client.post(
        "/fines/import",
        headers=_import_headers(),
        json=[
            {"plate": "01KG139API", "external_ref": "AM1", "amount": "500.00"},
            {"plate": "09KG000ZZZ", "external_ref": "AM2"},
        ],
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "created": 1,
        "skipped": 0,
        "unknown_plates": ["09KG000ZZZ"],
        "ambiguous_plates": [],
    }
