"""Celery-задачи block_overdue / unblock_paid: _run над доменом и обёртка.

Полную задачу с record_run тут не гоняем (её sync-движок открыл бы отдельную
in-memory БД); проверяем `_run` с подменённым session_scope и обёртку с
подменёнными run_async/record_run.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    Car,
    CarState,
    Command,
    CommandStatus,
    CommandType,
    SchedulePeriod,
    TaskRunStatus,
    Tracker,
    TrackerProvider,
)
from app.domain import commands as commands_domain
from app.domain import drivers as drivers_service
from app.domain import engine_enforcement as enforcement
from app.domain import schedules as sched
from app.routers.admin import KNOWN_TASKS
from app.tasks import payments_block, payments_unblock

PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def adapter_ok(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def _send(external_id: str, command: str, params=None):
        sent.append((external_id, command))
        return {"status": "sent", "result": "S20,OK"}

    monkeypatch.setattr(commands_domain, "send_command", _send)
    return sent


def _scope_over(session):
    """Подмена session_scope: отдаёт тестовую сессию и не закрывает её."""

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Scope()


async def _car(session, *, plate, external_id, engine_blocked=False):
    car = Car(plate=plate)
    session.add(car)
    await session.flush()
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id=external_id
    )
    session.add(tracker)
    await session.flush()
    session.add(
        CarState(
            car_id=car.id, tracker_id=tracker.id,
            last_ts=datetime.now(timezone.utc), speed_knots=0.0,
            ignition=False, motion=False, engine_blocked=engine_blocked,
        )
    )
    await session.commit()
    return car, tracker


async def _driver(session, car_id, *, tg_user_id, due):
    driver = await drivers_service.register_driver(
        session, tg_user_id=tg_user_id, full_name="Иванов", phone="+1",
        inn=str(tg_user_id), selfie_file_id=None, selfie_path=None, car_id=car_id,
    )
    await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.daily,
        interval_days=None, amount=1800.0, next_due_date=due,
    )
    await session.commit()
    return driver


def test_tasks_registered_in_known_tasks():
    assert "app.tasks.payments_block.block_overdue" in KNOWN_TASKS
    assert "app.tasks.payments_unblock.unblock_paid" in KNOWN_TASKS


async def test_block_run_arms_only_overdue(session, monkeypatch, adapter_ok):
    monkeypatch.setattr(payments_block, "session_scope", _scope_over(session))
    car1, _ = await _car(session, plate="AA", external_id="111")
    await _driver(session, car1.id, tg_user_id=1, due=PAST)  # просрочен
    car2, _ = await _car(session, plate="BB", external_id="222")
    await _driver(session, car2.id, tg_user_id=2, due=FUTURE)  # оплачен

    outcomes = await payments_block._run()

    assert outcomes == {"armed": 1}
    armed = await session.scalar(select(Command).where(Command.car_id == car1.id))
    assert armed.status is CommandStatus.armed and armed.source == "overdue"


async def test_block_run_isolates_per_car_errors(session, monkeypatch, adapter_ok):
    monkeypatch.setattr(payments_block, "session_scope", _scope_over(session))
    car, _ = await _car(session, plate="AA", external_id="111")
    await _driver(session, car.id, tg_user_id=1, due=PAST)

    async def _boom(*a, **k):
        raise RuntimeError("сбой адаптера")

    monkeypatch.setattr(enforcement, "enforce_overdue_block", _boom)

    outcomes = await payments_block._run()
    assert outcomes == {"errors": 1}


async def _blocked_paid_car(session, *, plate, external_id, tg_user_id):
    car, tracker = await _car(session, plate=plate, external_id=external_id, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=tg_user_id, due=FUTURE)  # оплачен
    session.add(Command(
        car_id=car.id, tracker_id=tracker.id, type=CommandType.engine_stop,
        status=CommandStatus.acked, source="overdue",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    await session.commit()
    return car


async def test_unblock_run_releases_paid(session, monkeypatch, adapter_ok):
    monkeypatch.setattr(payments_unblock, "session_scope", _scope_over(session))
    await _blocked_paid_car(session, plate="AA", external_id="111", tg_user_id=1)

    outcomes = await payments_unblock._run()

    assert outcomes == {"unblocked": 1}
    assert ("111", "engine_resume") in adapter_ok


async def test_unblock_run_isolates_per_car_errors(monkeypatch):
    """Падение на одной машине не прерывает цикл: вторая обрабатывается, на упавшей —
    rollback, на успешной — commit. Логику изоляции проверяем на фейковой сессии,
    чтобы не упираться в особенности общей in-memory aiosqlite (в проде сессия —
    отдельный asyncpg-коннект, как в fire_armed)."""
    commits = rollbacks = 0

    class _FakeSession:
        async def commit(self):
            nonlocal commits
            commits += 1

        async def rollback(self):
            nonlocal rollbacks
            rollbacks += 1

    class _Scope:
        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(payments_unblock, "session_scope", lambda: _Scope())

    async def _cars(sess, source):
        return [1, 2]

    monkeypatch.setattr(enforcement, "cars_under_system_block", _cars)

    async def _release(sess, *, car_id, now=None):
        if car_id == 1:
            raise RuntimeError("сбой на первой машине")
        return enforcement.ReleaseOutcome.unblocked

    monkeypatch.setattr(enforcement, "release_if_paid", _release)

    outcomes = await payments_unblock._run()
    assert outcomes == {"errors": 1, "unblocked": 1}, "обе машины обработаны, одна упала"
    assert rollbacks == 1 and commits == 1, "упавшую откатили, успешную зафиксировали"


def test_block_wrapper_sets_detail_and_payload(monkeypatch):
    captured = {}

    @contextmanager
    def _fake_record_run(task, periodic_task_id=None, run_id=None):
        class _Run:
            pass

        run = _Run()
        yield run
        captured["status"] = run.status
        captured["detail"] = run.detail
        captured["payload"] = run.payload

    monkeypatch.setattr(payments_block, "record_run", _fake_record_run)
    monkeypatch.setattr(payments_block, "run_async", lambda factory: {"armed": 2, "skipped-blocked": 1})

    result = payments_block.block_overdue()

    assert result == {"armed": 2, "skipped-blocked": 1}
    assert captured["detail"] == "просрочено 3, взведено 2"
    assert captured["payload"] == {"armed": 2, "skipped-blocked": 1}
    assert captured["status"] is TaskRunStatus.ok


def test_unblock_wrapper_sets_detail_and_payload(monkeypatch):
    captured = {}

    @contextmanager
    def _fake_record_run(task, periodic_task_id=None, run_id=None):
        class _Run:
            pass

        run = _Run()
        yield run
        captured["status"] = run.status
        captured["detail"] = run.detail
        captured["payload"] = run.payload

    monkeypatch.setattr(payments_unblock, "record_run", _fake_record_run)
    monkeypatch.setattr(payments_unblock, "run_async", lambda factory: {"unblocked": 1, "still-overdue": 2})

    result = payments_unblock.unblock_paid()

    assert result == {"unblocked": 1, "still-overdue": 2}
    assert captured["detail"] == "под блоком 3, разблокировано 1"
    assert captured["payload"] == {"unblocked": 1, "still-overdue": 2}
    assert captured["status"] is TaskRunStatus.ok
