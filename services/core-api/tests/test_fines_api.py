"""Новые маршруты штрафов: список по парку, карточка и запуск проверки."""
from __future__ import annotations

from decimal import Decimal

from app.db.models import TaskRunStatus
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.domain import periodic as periodic_service
from app.tasks.fines_tolom import NAME

from tests.conftest import ADMIN_ID, CORE_TOKEN


def _core_headers(actor=ADMIN_ID):
    return {"Authorization": f"Bearer {CORE_TOKEN}", "X-TG-User-Id": str(actor)}


async def _car(session, plate="01KG100AAA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


async def _fine(session, car, ref="R1", **kwargs):
    await fines_service.import_fines(
        session,
        [fines_service.FineImportRow(plate=car.plate, external_ref=ref, **kwargs)],
        source="tolom",
    )
    return (await fines_service.list_fines(session, car.id))[0]


# --- карточка ---------------------------------------------------------------


async def test_fine_card_carries_details_and_plate(client, session, admin_headers):
    car = await _car(session)
    fine = await _fine(
        session,
        car,
        amount=Decimal("1000"),
        amount_to_pay=Decimal("300"),
        article="Ст. 187 ч. 1",
        violation_title="превышение скорости",
        place="а/д Балыкчы-Каракол",
        payment_code="1000000000000000001",
    )

    resp = await client.get(f"/fines/{fine.id}", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["external_ref"] == "R1"
    assert body["car_plate"] == car.plate
    assert body["article"] == "Ст. 187 ч. 1"
    assert body["place"] == "а/д Балыкчы-Каракол"
    assert body["payment_code"] == "1000000000000000001"


async def test_missing_fine_is_404(client, admin_headers):
    resp = await client.get("/fines/999999", headers=admin_headers)

    assert resp.status_code == 404


# --- список по парку --------------------------------------------------------


async def test_fleet_list_groups_plates_and_hides_paid(client, session, admin_headers):
    first = await _car(session, "01KG100AAA")
    second = await _car(session, "01KG200BBB")
    await _fine(session, first, "R1")
    await _fine(session, second, "R2")
    paid = await _fine(session, second, "R3")
    await fines_service.pay_fine(session, paid.id)

    resp = await client.get("/fines", headers=_core_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert [f["external_ref"] for f in body] == ["R1", "R2"]
    assert body[0]["car_plate"] == "01KG100AAA"


async def test_fleet_list_can_show_paid_too(client, session, admin_headers):
    car = await _car(session)
    fine = await _fine(session, car)
    await fines_service.pay_fine(session, fine.id)

    resp = await client.get("/fines?only_unpaid=false", headers=_core_headers())

    assert [f["external_ref"] for f in resp.json()] == ["R1"]


async def test_fleet_list_requires_admin(client, session):
    """Список по всему парку — админский экран."""
    resp = await client.get("/fines", headers=_core_headers(actor=999))

    assert resp.status_code == 403


# --- запуск проверки --------------------------------------------------------


async def test_check_queues_a_run_and_returns_it(client, session, monkeypatch):
    from app.tasks.celery_app import celery_app

    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: sent.append((a, k)))

    resp = await client.post("/fines/check", headers=_core_headers())

    assert resp.status_code == 202
    body = resp.json()
    assert body["task"] == NAME
    assert body["finished_at"] is None, "прогон ещё идёт"
    # Задача должна получить id заранее заведённой строки: иначе свой прогон
    # не отличить от кронового, начавшегося в ту же секунду.
    assert sent[0][1]["kwargs"] == {"run_id": body["id"]}


async def test_second_tap_returns_the_same_run(client, session, monkeypatch):
    """Двойной тап не должен ни копить очередь, ни лишний раз бить по сервису."""
    from app.tasks.celery_app import celery_app

    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: sent.append(k))

    first = (await client.post("/fines/check", headers=_core_headers())).json()
    second = (await client.post("/fines/check", headers=_core_headers())).json()

    assert first["id"] == second["id"]
    assert len(sent) == 1


async def test_broken_broker_is_reported_not_swallowed(client, session, monkeypatch):
    from app.tasks.celery_app import celery_app

    def boom(*args, **kwargs):
        raise ConnectionError("брокер недоступен")

    monkeypatch.setattr(celery_app, "send_task", boom)

    resp = await client.post("/fines/check", headers=_core_headers())

    assert resp.status_code == 503
    run = await periodic_service.last_run(session, NAME)
    assert run.finished_at is not None, "прогон закрыт, а не завис навсегда"


async def test_check_requires_admin(client):
    resp = await client.post("/fines/check", headers=_core_headers(actor=999))

    assert resp.status_code == 403


# --- чтение прогона ---------------------------------------------------------


async def test_single_run_is_readable(client, session, admin_headers):
    run, _ = await periodic_service.start_run(session, task=NAME, requested_by=ADMIN_ID)

    resp = await client.get(f"/task-runs/{run.id}", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["id"] == run.id


async def test_single_run_requires_admin(client, session):
    run, _ = await periodic_service.start_run(session, task=NAME)

    resp = await client.get(f"/task-runs/{run.id}", headers=_core_headers(actor=999))

    assert resp.status_code == 403


# --- гонка при двойном нажатии ----------------------------------------------


async def test_second_start_does_not_create_a_parallel_run(session_maker):
    """Два нажатия «Проверить сейчас» — один обход парка.

    Прошлый тест звал ручку последовательно и полагался на проверку «идёт ли
    прогон», а между такой проверкой и вставкой помещается второй запрос.
    Теперь `start_run` вставляет сразу, и запрет держит уникальный индекс —
    именно это здесь и проверяется, двумя независимыми сессиями.

    Настоящую одновременность на общей in-memory базе не изобразить, но она и
    не нужна: важно, что вторая вставка отбивается индексом, а не тем, успела
    ли первая сессия закоммититься до чтения.
    """
    async with session_maker() as first_session, session_maker() as second_session:
        first, created_first = await periodic_service.start_run(
            first_session, task=NAME
        )
        second, created_second = await periodic_service.start_run(
            second_session, task=NAME
        )

    assert created_first is True
    assert created_second is False, "второй прогон завести нельзя, пока идёт первый"
    assert second.id == first.id


async def test_second_run_waits_for_the_first_to_finish(client, session, monkeypatch):
    from app.tasks.celery_app import celery_app

    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: None)
    first = (await client.post("/fines/check", headers=_core_headers())).json()

    run = await periodic_service.get_run(session, first["id"])
    run.finished_at = run.started_at
    run.status = TaskRunStatus.ok
    await session.commit()

    second = (await client.post("/fines/check", headers=_core_headers())).json()

    assert second["id"] != first["id"], "прогон закончился — новый запуск разрешён"


async def test_stale_run_does_not_block_forever(session):
    """Убитый воркер оставляет строку незавершённой, а та держит уникальный индекс."""
    from datetime import datetime, timedelta, timezone

    run, _ = await periodic_service.start_run(session, task=NAME)
    run.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await session.commit()

    closed = await periodic_service.close_stale_runs(
        session, older_than=timedelta(minutes=45)
    )

    assert closed == 1
    fresh, created = await periodic_service.start_run(session, task=NAME)
    assert created, "после чистки запуск снова возможен"
    assert fresh.id != run.id
