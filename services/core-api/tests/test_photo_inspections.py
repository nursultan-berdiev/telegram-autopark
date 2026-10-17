"""Фотоконтроль: срок, сдача, ежедневные напоминания и алерт админу о просрочке."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db.models import (
    AlertStatus,
    AlertType,
    Car,
    Driver,
    Invitation,
    InviteStatus,
    OutboundMessage,
    PhotoInspection,
    PhotoSchedule,
)
from app.domain import alerts as alerts_domain
from app.domain import photo_inspections as photo
from app.errors import Conflict, DomainError, Validation
from app.routers.admin import KNOWN_TASKS
from app.tasks import photo_inspections as photo_task

NOW = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)  # 12:00 по Бишкеку
TODAY = date(2026, 10, 17)
PHOTOS = {
    "front_file_id": "F",
    "rear_file_id": "R",
    "interior_front_file_id": "IF",
    "interior_rear_file_id": "IR",
}


def _day(n: int) -> datetime:
    return NOW + timedelta(days=n)


async def _driver(session, *, tg=501, plate="01KG001AAA", active=True, with_car=True) -> Driver:
    car = Car(plate=plate)
    session.add(car)
    await session.flush()
    driver = Driver(
        tg_user_id=tg, full_name=f"Вод {tg}", phone="+1", inn=str(tg),
        car_id=car.id if with_car else None, active=active,
    )
    session.add(driver)
    await session.commit()
    return driver


async def _set_due(session, driver, due: date) -> None:
    session.add(PhotoSchedule(driver_id=driver.id, next_due_on=due))
    await session.commit()


async def _set_due_existing(session, driver, due: date) -> None:
    schedule = await session.scalar(select(PhotoSchedule).where(PhotoSchedule.driver_id == driver.id))
    schedule.next_due_on = due
    await session.commit()


async def _outbox(session) -> list[OutboundMessage]:
    return list(await session.scalars(select(OutboundMessage).order_by(OutboundMessage.id)))


async def _open_photo_alerts(session):
    return [
        a for a in await alerts_domain.list_alerts(session, status="open")
        if a.type == AlertType.photo_inspection_overdue
    ]


async def _submit(session, tg=501, *, now=NOW, has_issues=False, comment=None, **photos):
    return await photo.submit(
        session, tg_user_id=tg, has_issues=has_issues, comment=comment, now=now,
        **{**PHOTOS, **photos},
    )


# --- статус -----------------------------------------------------------------


async def test_status_without_schedule_is_due_tomorrow_and_read_only(session):
    driver = await _driver(session)
    await _driver(session, tg=502, plate="02KG", with_car=False)  # без машины — вне фотоконтроля
    await _driver(session, tg=503, plate="03KG", active=False)

    (status,) = await photo.statuses(session, NOW)

    assert status.driver_id == driver.id and status.next_due_on == TODAY + timedelta(days=1)
    assert status.overdue_days == 0 and status.last_inspection_id is None
    assert await session.scalar(select(PhotoSchedule)) is None


def test_due_day_itself_is_not_overdue():
    assert photo.overdue_days(TODAY, TODAY) == 0
    assert photo.overdue_days(TODAY, TODAY + timedelta(days=3)) == 3


# --- ежедневный проход ------------------------------------------------------


async def test_first_run_starts_schedule_and_reminds_once_a_day(session):
    driver = await _driver(session)

    first = await photo.run_daily(session, NOW)
    await session.commit()
    again = await photo.run_daily(session, NOW + timedelta(hours=2))  # «Проверить сейчас»
    await session.commit()

    schedule = await session.scalar(select(PhotoSchedule))
    assert schedule.driver_id == driver.id and schedule.next_due_on == TODAY + timedelta(days=1)
    assert first["started"] == 1 and first["reminders"] == 1 and "reminders" not in again
    (msg,) = await _outbox(session)
    assert msg.recipient_tg_user_id == 501 and msg.kind == photo.REMINDER_KIND
    assert "Завтра срок фотоконтроля машины 01KG001AAA" in msg.text
    assert await _open_photo_alerts(session) == []


async def test_due_today_reminds_without_alert(session):
    driver = await _driver(session)
    await _set_due(session, driver, TODAY)

    out = await photo.run_daily(session, NOW)

    assert out["reminders"] == 1 and "overdue" not in out
    assert "Сегодня срок" in (await _outbox(session))[0].text
    assert await _open_photo_alerts(session) == []


async def test_overdue_reminds_driver_and_alerts_admin_once(session):
    driver = await _driver(session)
    await _set_due(session, driver, TODAY - timedelta(days=2))

    await photo.run_daily(session, NOW)
    await session.commit()
    (alert,) = await _open_photo_alerts(session)
    await alerts_domain.mark_notified(session, alert, NOW)
    await session.commit()
    await photo.run_daily(session, _day(1))  # назавтра
    await session.commit()

    texts = [m.text for m in await _outbox(session)]
    assert "просрочен на 2 дн." in texts[0] and "просрочен на 3 дн." in texts[1]
    (still,) = await _open_photo_alerts(session)
    # Тот же открытый алерт: доставлен один раз, текст обновлён.
    assert still.id == alert.id and still.notified_at is not None
    assert still.payload["overdue_days"] == 3
    assert "водитель Вод 501" in still.payload["text"]


async def test_acknowledged_alert_comes_back_next_day(session):
    driver = await _driver(session)
    await _set_due(session, driver, TODAY - timedelta(days=1))
    await photo.run_daily(session, NOW)
    (alert,) = await _open_photo_alerts(session)
    await alerts_domain.set_status(session, alert, AlertStatus.acknowledged)
    await session.commit()

    await photo.run_daily(session, _day(1))

    (fresh,) = await _open_photo_alerts(session)
    assert fresh.id != alert.id


async def test_alert_closes_when_driver_fired(session):
    driver = await _driver(session)
    await _set_due(session, driver, TODAY - timedelta(days=1))
    await photo.run_daily(session, NOW)
    await session.commit()
    driver.active, driver.car_id = False, None
    await session.commit()

    out = await photo.run_daily(session, _day(1))

    assert out["resolved"] == 1 and await _open_photo_alerts(session) == []


# --- сдача ------------------------------------------------------------------


async def test_submit_moves_due_and_closes_alert(session):
    driver = await _driver(session)
    await _set_due(session, driver, TODAY - timedelta(days=2))
    await photo.run_daily(session, NOW)
    await session.commit()

    inspection, schedule = await _submit(session, has_issues=True, comment="  скол на бампере ")

    assert inspection.car_id == driver.car_id and inspection.comment == "скол на бампере"
    assert inspection.front_file_id == "F" and inspection.interior_rear_file_id == "IR"
    assert schedule.next_due_on == TODAY + timedelta(days=settings.photo_inspection_interval_days)
    assert await _open_photo_alerts(session) == []
    (status,) = await photo.statuses(session, NOW)
    assert status.last_inspection_id == inspection.id and status.overdue_days == 0


async def test_early_submit_counts_from_submission_day(session):
    driver = await _driver(session)
    await _set_due(session, driver, TODAY + timedelta(days=5))

    _, schedule = await _submit(session)

    assert schedule.next_due_on == TODAY + timedelta(days=settings.photo_inspection_interval_days)


async def test_submit_rejects_non_drivers_and_missing_report(session):
    await _driver(session, tg=501)
    await _driver(session, tg=502, plate="02KG", active=False)
    await _driver(session, tg=503, plate="03KG", with_car=False)

    for tg in (None, 111, 502):  # без актора, админ, уволенный
        with pytest.raises(DomainError) as err:
            await _submit(session, tg)
        assert err.value.status_code == 403
    with pytest.raises(Conflict):
        await _submit(session, 503)
    with pytest.raises(Validation):
        await _submit(session, 501, has_issues=True, comment="   ")


async def test_repeated_submit_of_same_photos_is_idempotent(session):
    """Бот переспросил после таймаута — второго осмотра и второго альбома быть не должно."""
    await _driver(session)
    first, _ = await _submit(session)
    again, schedule = await _submit(session, now=NOW + timedelta(minutes=1))
    other, _ = await _submit(session, front_file_id="F2")

    assert again.id == first.id and other.id != first.id
    assert schedule.next_due_on == TODAY + timedelta(days=settings.photo_inspection_interval_days)


async def test_same_photos_after_window_are_a_new_submission(session):
    """Старые file_id после окна — не «повтор»: иначе водитель с просрочкой получил бы
    «принято», а срок и алерт остались бы прежними."""
    driver = await _driver(session)
    first, _ = await _submit(session)
    await _set_due_existing(session, driver, TODAY - timedelta(days=2))
    later = NOW + photo.REPEAT_WINDOW + timedelta(minutes=1)

    again, schedule = await _submit(session, now=later)

    assert again.id != first.id
    assert schedule.next_due_on == photo.local_today(later) + timedelta(
        days=settings.photo_inspection_interval_days
    )


@pytest.mark.parametrize(
    "delay,is_repeat",
    [
        (photo.REPEAT_WINDOW, True),
        (photo.REPEAT_WINDOW + timedelta(seconds=1), False),
    ],
    ids=["ровно окно — повтор", "окно + 1 с — новая сдача"],
)
async def test_repeat_window_boundary(session, delay, is_repeat):
    await _driver(session)
    first, schedule = await _submit(session)
    due_after_first = schedule.next_due_on

    again, schedule = await _submit(session, now=NOW + delay)

    count = await session.scalar(select(func.count()).select_from(PhotoInspection))
    assert (again.id == first.id) is is_repeat
    assert count == (1 if is_repeat else 2)
    if is_repeat:
        assert schedule.next_due_on == due_after_first  # повтор срок не двигает


PAIRS = [
    (a, b)
    for i, a in enumerate(PHOTOS)
    for b in list(PHOTOS)[i + 1 :]
]


@pytest.mark.parametrize("first,second", PAIRS, ids=[f"{a}={b}" for a, b in PAIRS])
async def test_same_photo_on_two_angles_is_rejected(session, first, second):
    await _driver(session)

    with pytest.raises(Validation):
        await _submit(session, **{second: PHOTOS[first]})


async def test_concurrent_schedule_creation_reuses_existing_row(session, monkeypatch):
    """Встречная вставка графика (сдача и ежедневный проход разом) не роняет вызов."""
    driver = await _driver(session)
    await _set_due(session, driver, TODAY + timedelta(days=3))
    real = photo._schedule
    calls = {"n": 0}

    async def _stale_first(sess, driver_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(sess, driver_id)

    monkeypatch.setattr(photo, "_schedule", _stale_first)

    schedule = await photo._ensure_schedule(session, driver.id, due=TODAY)

    assert schedule.next_due_on == TODAY + timedelta(days=3)  # чужая строка, не наша вставка


async def test_one_failing_driver_does_not_stop_others(session, monkeypatch):
    await _driver(session, tg=501, plate="01KG")
    await _driver(session, tg=502, plate="02KG")
    real = photo.outbox.enqueue

    async def _flaky(sess, *, recipient_tg_user_id, **kw):
        if recipient_tg_user_id == 501:
            raise RuntimeError("сбой")
        return await real(sess, recipient_tg_user_id=recipient_tg_user_id, **kw)

    monkeypatch.setattr(photo.outbox, "enqueue", _flaky)

    out = await photo.run_daily(session, NOW)
    await session.commit()

    assert out["errors"] == 1 and out["reminders"] == 1
    assert [m.recipient_tg_user_id for m in await _outbox(session)] == [502]
    schedules = {s.driver_id: s for s in await session.scalars(select(PhotoSchedule))}
    assert len(schedules) == 1  # график сбойного водителя откатился вместе с его SAVEPOINT


async def test_failure_keeps_existing_alert_open(session, monkeypatch):
    """Сбой по водителю в проходе не должен молча закрыть его настоящий алерт."""
    driver = await _driver(session)
    await _set_due(session, driver, TODAY - timedelta(days=2))
    await photo.run_daily(session, NOW)
    await session.commit()
    (alert,) = await _open_photo_alerts(session)

    async def _boom(*_a, **_k):
        raise RuntimeError("сбой")

    monkeypatch.setattr(photo.outbox, "enqueue", _boom)
    out = await photo.run_daily(session, _day(1))

    assert out["errors"] == 1 and "resolved" not in out
    assert [a.id for a in await _open_photo_alerts(session)] == [alert.id]


async def test_car_overdue_days_feeds_engine_predicate(session):
    driver = await _driver(session)
    lonely = Car(plate="FREE")
    session.add(lonely)
    await session.commit()
    await _set_due(session, driver, TODAY - timedelta(days=4))

    assert await photo.car_overdue_days(session, driver.car_id, NOW) == 4
    assert await photo.car_overdue_days(session, lonely.id, NOW) is None


async def test_restart_schedule_forgets_old_overdue(session):
    driver = await _driver(session)
    await _set_due(session, driver, TODAY - timedelta(days=30))

    schedule = await photo.restart_schedule(session, driver.id, TODAY)

    assert schedule.next_due_on == TODAY + timedelta(days=1) and schedule.last_reminded_on is None


# --- задача и регистрация ---------------------------------------------------


def _scope_rollback(session):
    """Как настоящий session_scope: незакоммиченное теряется на выходе."""

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            await session.rollback()
            return False

    return lambda: _Scope()


async def test_task_commits_reminders(session, monkeypatch):
    monkeypatch.setattr(photo_task, "session_scope", _scope_rollback(session))
    await _driver(session)

    outcomes = await photo_task._run()

    assert outcomes["reminders"] == 1
    assert len(await _outbox(session)) == 1


def test_reminder_task_is_schedulable_but_photo_block_is_not():
    """Блокировка за фотоконтроль — отдельное решение владельца: задачу не знает
    ни воркер, ни админка."""
    from app.tasks.celery_app import celery_app

    assert photo_task.NAME in KNOWN_TASKS
    assert "app.tasks.photo_inspections" in celery_app.conf.include
    assert "app.tasks.photo_enforcement" not in celery_app.conf.include
    assert not any("photo_enforcement" in name for name in KNOWN_TASKS)


async def test_registration_restarts_photo_schedule(client, session):
    driver = await _driver(session, tg=501)
    await _set_due(session, driver, TODAY - timedelta(days=30))
    driver.active, driver.car_id = False, None
    car = Car(plate="NEWCAR")
    session.add(car)
    await session.flush()
    session.add(
        Invitation(
            code="re-hire", car_id=car.id, created_by=111,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            status=InviteStatus.active,
        )
    )
    await session.commit()

    response = await client.post(
        "/drivers/register",
        json={"code": "re-hire", "tg_user_id": 501, "full_name": "Вернулся", "phone": "+1", "inn": "1"},
    )

    assert response.status_code == 200
    session.expire_all()
    schedule = await session.scalar(select(PhotoSchedule))
    assert schedule.next_due_on == photo.local_today(datetime.now(timezone.utc)) + timedelta(days=1)


# --- API --------------------------------------------------------------------


def _body(**over) -> dict:
    return {**PHOTOS, "has_issues": False, **over}


async def test_api_submit_and_read(client, session):
    driver = await _driver(session)

    created = await client.post("/photo-inspections", json=_body(), headers={"X-TG-User-Id": "501"})
    assert created.status_code == 201
    body = created.json()
    assert body["inspection"]["car_id"] == driver.car_id
    interval = timedelta(days=settings.photo_inspection_interval_days)
    expected_due = photo.local_today(datetime.now(timezone.utc)) + interval
    assert body["next_due_on"] == expected_due.isoformat()

    inspection_id = body["inspection"]["id"]
    one = await client.get(f"/photo-inspections/{inspection_id}")
    assert one.json()["rear_file_id"] == "R"
    assert one.json()["driver_name"] == "Вод 501" and one.json()["car_plate"] == "01KG001AAA"
    history = await client.get(f"/drivers/{driver.id}/photo-inspections")
    assert [i["id"] for i in history.json()] == [inspection_id]
    fleet = await client.get("/photo-inspections/status")
    assert fleet.json()[0]["last_inspection_id"] == inspection_id
    mine = await client.get(f"/drivers/{driver.id}/photo-status")
    assert mine.json()["car_plate"] == "01KG001AAA" and mine.json()["overdue_days"] == 0


@pytest.mark.parametrize("actor", [None, "111"], ids=["без актора", "админ"])
async def test_api_submit_only_by_driver(client, session, actor):
    await _driver(session)
    headers = {"X-TG-User-Id": actor} if actor else {}

    response = await client.post("/photo-inspections", json=_body(), headers=headers)

    assert response.status_code == 403


@pytest.mark.parametrize(
    "bad,field",
    [
        (_body(front_file_id=""), "front_file_id"),
        (_body(rear_file_id="x" * 257), "rear_file_id"),
        (_body(comment="я" * 2001), "comment"),
        ({k: v for k, v in _body().items() if k != "interior_rear_file_id"}, "interior_rear_file_id"),
    ],
    ids=["пустое фото", "длинный file_id", "длинный комментарий", "нет ракурса"],
)
async def test_api_submit_contract_validation(client, session, bad, field):
    """Контракт: 422 именно по тому полю, а не любой."""
    await _driver(session)

    response = await client.post("/photo-inspections", json=bad, headers={"X-TG-User-Id": "501"})

    assert response.status_code == 422
    assert [e["loc"][-1] for e in response.json()["detail"]] == [field]


@pytest.mark.parametrize(
    "bad,detail",
    [
        (_body(has_issues=True), "опишите замечания по состоянию машины"),
        (
            _body(interior_rear_file_id="F"),
            "одно и то же фото на разных ракурсах — нужен каждый ракурс отдельно",
        ),
    ],
    ids=["без описания", "повтор фото"],
)
async def test_api_submit_domain_validation(client, session, bad, detail):
    await _driver(session)

    response = await client.post("/photo-inspections", json=bad, headers={"X-TG-User-Id": "501"})

    assert response.status_code == 422 and response.json()["detail"] == detail


async def test_api_not_found(client):
    assert (await client.get("/photo-inspections/999")).status_code == 404
    assert (await client.get("/drivers/999/photo-status")).status_code == 404
    assert (await client.get("/drivers/999/photo-inspections")).json() == []
