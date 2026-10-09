"""Авто-блокировка двигателя при > N неоплаченных штрафов и снятие при погашении.

Политика fines переиспользует обобщённое ядро engine_enforcement; здесь проверяем
порог (строго >), источник fines (не пересекается с арендой/ручным), снятие и
задачу enforce_fines.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    Car,
    CarState,
    Command,
    CommandSource,
    CommandStatus,
    CommandType,
    Fine,
    FineStatus,
    TaskRunStatus,
    Tracker,
    TrackerProvider,
)
from app.domain import alerts as alerts_domain
from app.domain import commands as commands_domain
from app.domain import engine_enforcement as enforcement
from app.domain import fines as fines_domain
from app.domain.engine_enforcement import BlockOutcome, ReleaseOutcome
from app.tasks import fines_enforcement

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
POLICY = enforcement.fines_policy(5)  # блок при > 5 (т.е. 6+)


@pytest.fixture(autouse=True)
def adapter_ok(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def _send(external_id, command, params=None):
        sent.append((external_id, command))
        return {"status": "sent", "result": "S20,OK"}

    monkeypatch.setattr(commands_domain, "send_command", _send)
    return sent


async def _car(session, *, plate="01KG555AAA", external_id="9175358042", engine_blocked=False):
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
            car_id=car.id, tracker_id=tracker.id, last_ts=NOW, speed_knots=0.0,
            ignition=False, motion=False, engine_blocked=engine_blocked,
        )
    )
    await session.commit()
    return car, tracker


async def _add_unpaid(session, car_id, n):
    for i in range(n):
        session.add(Fine(car_id=car_id, issued_at=NOW, status=FineStatus.unpaid, external_ref=f"{car_id}-{i}"))
    await session.commit()


async def _fines_block(session, car, tracker, *, status, source=CommandSource.fines):
    cmd = Command(
        car_id=car.id, tracker_id=tracker.id, type=CommandType.engine_stop,
        status=status, source=source, created_at=NOW, updated_at=NOW,
    )
    session.add(cmd)
    await session.commit()
    return cmd


async def _open_types(session):
    return [a.type.value for a in await alerts_domain.list_alerts(session, status="open")]


# --- порог и блокировка ------------------------------------------------------


async def test_car_ids_over_unpaid_strict_threshold(session, adapter_ok):
    car6, _ = await _car(session, plate="AA", external_id="1")
    car5, _ = await _car(session, plate="BB", external_id="2")
    await _add_unpaid(session, car6.id, 6)
    await _add_unpaid(session, car5.id, 5)
    ids = await fines_domain.car_ids_over_unpaid(session, 5)
    assert ids == {car6.id}, "строго больше 5 → только с 6"


async def test_enforce_blocks_at_six(session, adapter_ok):
    car, _ = await _car(session)
    await _add_unpaid(session, car.id, 6)
    assert await enforcement.enforce_block(session, car_id=car.id, policy=POLICY, now=NOW) == BlockOutcome.armed
    await session.commit()
    cmd = await session.scalar(select(Command).where(Command.car_id == car.id))
    assert cmd.status is CommandStatus.armed and cmd.source == CommandSource.fines
    assert adapter_ok == []


async def test_enforce_skips_at_five(session, adapter_ok):
    car, _ = await _car(session)
    await _add_unpaid(session, car.id, 5)
    assert await enforcement.enforce_block(session, car_id=car.id, policy=POLICY, now=NOW) == BlockOutcome.not_applicable
    assert await session.scalar(select(Command).where(Command.car_id == car.id)) is None


async def test_fire_armed_fines_block_raises_fines_alert(session, adapter_ok):
    car, _ = await _car(session)
    await _add_unpaid(session, car.id, 6)
    await enforcement.enforce_block(session, car_id=car.id, policy=POLICY, now=NOW)
    await session.commit()
    assert await commands_domain.fire_armed(session, car_ids=[car.id], now=NOW) == 1
    assert await _open_types(session) == ["fines_block_fired"]


# --- снятие ------------------------------------------------------------------


async def test_release_when_fines_paid_down(session, adapter_ok):
    car, tracker = await _car(session, engine_blocked=True)
    await _add_unpaid(session, car.id, 3)  # ≤ порога
    await _fines_block(session, car, tracker, status=CommandStatus.acked)
    assert await enforcement.release_if_cleared(session, car_id=car.id, policy=POLICY, now=NOW) == ReleaseOutcome.unblocked
    await session.commit()
    assert ("9175358042", "engine_resume") in adapter_ok
    assert "fines_unblock" in await _open_types(session)


async def test_release_keeps_block_while_over_threshold(session, adapter_ok):
    car, tracker = await _car(session, engine_blocked=True)
    await _add_unpaid(session, car.id, 7)  # всё ещё > 5
    await _fines_block(session, car, tracker, status=CommandStatus.acked)
    assert await enforcement.release_if_cleared(session, car_id=car.id, policy=POLICY, now=NOW) == ReleaseOutcome.still_applies
    assert adapter_ok == []


async def test_release_failure_raises_fines_unblock_failed(session, adapter_ok, monkeypatch):
    async def _fail(external_id, command, params=None):
        return {"status": "failed", "result": "адаптер отклонил"}

    monkeypatch.setattr(commands_domain, "send_command", _fail)
    car, tracker = await _car(session, engine_blocked=True)
    await _add_unpaid(session, car.id, 0)
    await _fines_block(session, car, tracker, status=CommandStatus.acked)
    assert await enforcement.release_if_cleared(session, car_id=car.id, policy=POLICY, now=NOW) == ReleaseOutcome.unblock_failed
    await session.commit()
    types = await _open_types(session)
    assert "fines_unblock_failed" in types and "fines_unblock" not in types


# --- изоляция источников -----------------------------------------------------


async def test_overdue_release_ignores_fines_block(session, adapter_ok):
    """Снятие за аренду (source=overdue) не трогает блок за штрафы."""
    car, tracker = await _car(session, engine_blocked=True)
    await _fines_block(session, car, tracker, status=CommandStatus.acked)  # source=fines
    # OVERDUE-политика: активного системного overdue-блока у машины нет.
    assert await enforcement.release_if_cleared(session, car_id=car.id, policy=enforcement.OVERDUE, now=NOW) == ReleaseOutcome.no_system_block
    assert adapter_ok == []


async def test_manual_block_takes_over_fines_arm(session, adapter_ok):
    """Ручной блок забирает fines-взвод → авто-разблокировка за штрафы его не снимает."""
    car, tracker = await _car(session)
    await _add_unpaid(session, car.id, 3)  # уже погасили до ≤ порога
    sys_cmd = await _fines_block(session, car, tracker, status=CommandStatus.armed)
    cmd, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111, now=NOW,
    )
    await session.commit()
    assert cmd.id == sys_cmd.id and cmd.source == CommandSource.manual
    assert await enforcement.release_if_cleared(session, car_id=car.id, policy=POLICY, now=NOW) == ReleaseOutcome.no_system_block


# --- задача enforce_fines ----------------------------------------------------


def _scope_over(session):
    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Scope()


async def test_enforce_fines_run_blocks_over_threshold(session, monkeypatch, adapter_ok):
    monkeypatch.setattr(fines_enforcement, "session_scope", _scope_over(session))
    car, _ = await _car(session, plate="AA", external_id="1")
    await _add_unpaid(session, car.id, 6)
    other, _ = await _car(session, plate="BB", external_id="2")
    await _add_unpaid(session, other.id, 2)

    outcomes = await fines_enforcement._run(5)
    assert outcomes.get("block-armed") == 1
    cmd = await session.scalar(select(Command).where(Command.car_id == car.id))
    assert cmd.status is CommandStatus.armed and cmd.source == CommandSource.fines


def test_enforce_fines_wrapper_detail_and_payload(monkeypatch):
    captured = {}

    @contextmanager
    def _fake_record_run(task, periodic_task_id=None, run_id=None):
        class _Run:
            pass

        run = _Run()
        yield run
        captured.update(status=run.status, detail=run.detail, payload=run.payload)

    monkeypatch.setattr(fines_enforcement, "record_run", _fake_record_run)
    monkeypatch.setattr(fines_enforcement, "run_async", lambda factory: {"block-armed": 2, "release-unblocked": 1})

    result = fines_enforcement.enforce_fines(threshold=5)
    assert result == {"block-armed": 2, "release-unblocked": 1}
    assert captured["detail"] == "порог 5, взведено 2, разблокировано 1"
    assert captured["status"] is TaskRunStatus.ok


def test_enforce_fines_registered_in_known_tasks():
    from app.routers.admin import KNOWN_TASKS

    assert "app.tasks.fines_enforcement.enforce_fines" in KNOWN_TASKS
