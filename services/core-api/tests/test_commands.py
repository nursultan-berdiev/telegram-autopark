"""Гейт блокировки, идемпотентность, аудит и подтверждение команд."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    Car,
    CarState,
    Command,
    CommandStatus,
    Tracker,
    TrackerProvider,
)
from app.domain import commands as commands_domain


@pytest.fixture(autouse=True)
def adapter_ok(monkeypatch):
    """Реального адаптера в тестах нет — фиксируем успешную доставку."""
    sent: list[tuple[str, str]] = []

    async def _send(external_id: str, command: str, params=None):
        sent.append((external_id, command))
        return {"status": "sent", "result": "S20,OK"}

    monkeypatch.setattr(commands_domain, "send_command", _send)
    return sent


async def _car(session, *, speed=0.0, ignition=False, motion=False, fresh=True):
    car = Car(plate="01KG555AAA")
    session.add(car)
    await session.flush()
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id="9175358042"
    )
    session.add(tracker)
    await session.flush()
    last_ts = datetime.now(timezone.utc) - (
        timedelta(seconds=10) if fresh else timedelta(hours=3)
    )
    session.add(
        CarState(
            car_id=car.id,
            tracker_id=tracker.id,
            last_ts=last_ts,
            speed_knots=speed,
            ignition=ignition,
            motion=motion,
        )
    )
    await session.commit()
    return car, tracker


async def test_block_allowed_when_car_is_parked(session, adapter_ok):
    car, tracker = await _car(session)

    command, ok, reason = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    assert ok is True
    assert reason is None
    assert command.status is CommandStatus.sent
    assert adapter_ok == [("9175358042", "engine_stop")]


@pytest.mark.parametrize(
    "kwargs, expected_reason",
    [
        ({"speed": 12.0}, "машина в движении"),
        ({"motion": True}, "машина в движении"),
        ({"ignition": True}, "зажигание включено"),
        ({"fresh": False}, "нет свежей телеметрии — машина не на связи"),
    ],
)
async def test_gate_blocks_unsafe_states(session, adapter_ok, kwargs, expected_reason):
    """Блокировать едущую или потерянную машину нельзя ни при каких условиях."""
    car, _ = await _car(session, **kwargs)

    command, ok, reason = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    assert ok is False
    assert reason == expected_reason
    assert command.status is CommandStatus.blocked_by_safety
    assert command.safety_snapshot is not None
    assert adapter_ok == [], "команда на трекер уйти не должна"


async def test_unblock_ignores_gate(session, adapter_ok):
    """Разблокировать безопасно всегда — даже на ходу."""
    car, _ = await _car(session, speed=30.0, ignition=True, motion=True)

    _, ok, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_unblock", requested_by=111
    )
    await session.commit()

    assert ok is True
    assert adapter_ok == [("9175358042", "engine_resume")]


async def test_double_tap_sends_one_command(session, adapter_ok):
    car, _ = await _car(session)

    first, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111, alert_id=None
    )
    await session.commit()
    second, _, reason = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111, alert_id=None
    )
    await session.commit()

    assert second.id == first.id
    assert reason == "команда уже отправлена"
    assert len(adapter_ok) == 1


async def test_no_tracker_is_failed_command(session, adapter_ok):
    car = Car(plate="01KG000AAA")
    session.add(car)
    await session.commit()

    command, ok, reason = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    assert ok is False
    assert command.status is CommandStatus.failed
    assert "трекер" in reason


async def test_ack_comes_from_telemetry_bit(session, adapter_ok):
    car, _ = await _car(session)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    state = await session.get(CarState, car.id)
    state.engine_blocked = True
    state.last_ts = datetime.now(timezone.utc)  # точка пришла ПОСЛЕ команды
    await session.commit()

    confirmed = await commands_domain.confirm_by_telemetry(session)

    assert confirmed == 1
    assert (await session.get(Command, command.id)).status is CommandStatus.acked


async def test_silent_tracker_gets_unconfirmed_and_alert(session, adapter_ok):
    """Молчащий трекер: подтверждения не будет, админа предупреждает джоба."""
    car, _ = await _car(session)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    later = datetime.now(timezone.utc) + timedelta(hours=1)
    count = await commands_domain.sweep_unconfirmed(session, now=later)

    assert count == 1
    assert (await session.get(Command, command.id)).status is CommandStatus.unconfirmed

    from app.domain import alerts as alerts_domain

    alerts = await alerts_domain.list_alerts(session, status="open")
    assert [a.type.value for a in alerts] == ["command_unconfirmed"]


async def test_command_type_validation(session, adapter_ok):
    car, _ = await _car(session)

    with pytest.raises(ValueError):
        await commands_domain.request_command(
            session, car_id=car.id, type_value="самоуничтожение", requested_by=111
        )


async def test_stale_snapshot_does_not_confirm(session, adapter_ok):
    """Старый снимок не должен подтверждать только что отправленную команду."""
    car, _ = await _car(session)
    state = await session.get(CarState, car.id)
    # Машина числится заблокированной по СТАРОЙ точке (связь при этом свежая,
    # иначе гейт не пропустит команду).
    state.engine_blocked = True
    await session.commit()

    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    assert await commands_domain.confirm_by_telemetry(session) == 0
    assert (await session.get(Command, command.id)).status is CommandStatus.sent

    state = await session.get(CarState, car.id)
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()

    assert await commands_domain.confirm_by_telemetry(session) == 1


async def test_incomplete_telemetry_blocks_the_gate(session, adapter_ok):
    """Неизвестное состояние — не безопасное: кадр без зажигания и движения."""
    car, tracker = await _car(session)
    state = await session.get(CarState, car.id)
    state.ignition = None
    state.motion = None
    await session.commit()

    command, ok, reason = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    assert ok is False
    assert "неполные данные" in reason
    assert command.status is CommandStatus.blocked_by_safety
    assert adapter_ok == []


async def test_repeat_block_allowed_after_unblock(session, adapter_ok):
    """Подтверждённая старая блокировка не должна навсегда запрещать новую."""
    car, _ = await _car(session)
    first, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    first.status = CommandStatus.acked
    first.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    await session.commit()

    second, ok, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111
    )
    await session.commit()

    assert second.id != first.id
    assert ok is True


async def test_alarm_command_is_not_confirmed_by_engine_bit(session, adapter_ok):
    """У сигнализации нет бита в телеметрии — подтверждать её нечем."""
    car, _ = await _car(session)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="alarm_arm", requested_by=111
    )
    await session.commit()

    state = await session.get(CarState, car.id)
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()

    assert await commands_domain.confirm_by_telemetry(session) == 0
    assert (await session.get(Command, command.id)).status is CommandStatus.sent

    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert await commands_domain.sweep_unconfirmed(session, now=later) == 0, (
        "ложный алерт «нет реле» по команде сигнализации не нужен"
    )


async def test_command_sweep_runs_even_with_rules_disabled(monkeypatch):
    """Неподтверждённая блокировка — вопрос безопасности, а не движка правил.

    Тест асинхронный: AsyncIOScheduler требует запущенного событийного цикла.
    """
    from app import jobs
    from app.config import settings

    monkeypatch.setattr(settings, "rules_enabled", False)
    scheduler = jobs.start_jobs()
    try:
        ids = {job.id for job in scheduler.get_jobs()}
        assert "command_timeout" in ids
        assert "telemetry_cleanup" in ids
        assert "rules" not in ids
    finally:
        scheduler.shutdown(wait=False)
