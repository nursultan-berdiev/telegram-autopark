"""Гейт блокировки, идемпотентность, аудит и подтверждение команд."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    Car,
    CarState,
    Command,
    CommandStatus,
    CommandType,
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


async def test_block_arms_when_car_is_moving(session, adapter_ok):
    """arm_if_unsafe: едущую машину не отклоняем, а взводим — реле пока молчит."""
    car, _ = await _car(session, speed=20.0, motion=True)

    command, ok, reason = await commands_domain.request_command(
        session,
        car_id=car.id,
        type_value="engine_block",
        requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()

    assert ok is False
    assert reason == "машина в движении"
    assert command.status is CommandStatus.armed
    assert command.safety_snapshot is not None
    assert adapter_ok == [], "взвод не должен слать на реле"


async def test_armed_block_fires_when_car_stops(session, adapter_ok):
    car, _ = await _car(session, speed=20.0, motion=True)
    command, _, _ = await commands_domain.request_command(
        session,
        car_id=car.id,
        type_value="engine_block",
        requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()

    # Машина встала и прислала свежую точку.
    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()

    fired = await commands_domain.fire_armed(session, car_ids=[car.id])

    assert fired == 1
    assert adapter_ok == [("9175358042", "engine_stop")]
    assert (await session.get(Command, command.id)).status is CommandStatus.sent

    from app.domain import alerts as alerts_domain

    alerts = await alerts_domain.list_alerts(session, status="open")
    assert [a.type.value for a in alerts] == ["armed_block_fired"]


async def test_armed_block_waits_while_still_moving(session, adapter_ok):
    car, _ = await _car(session, speed=20.0, motion=True)
    command, _, _ = await commands_domain.request_command(
        session,
        car_id=car.id,
        type_value="engine_block",
        requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()

    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 0
    assert adapter_ok == []
    assert (await session.get(Command, command.id)).status is CommandStatus.armed


async def test_second_arm_returns_same_command(session, adapter_ok):
    """Повторный взвод не плодит дублей — возвращает ту же ожидающую команду."""
    car, _ = await _car(session, speed=20.0, motion=True)
    first, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()
    second, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()

    assert second.id == first.id
    assert second.status is CommandStatus.armed


async def test_cancel_armed_block(session, adapter_ok):
    car, _ = await _car(session, speed=20.0, motion=True)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()

    cancelled, ok, _ = await commands_domain.cancel_armed(
        session, car_id=car.id, command_id=command.id
    )
    await session.commit()

    assert ok is True
    assert cancelled.status is CommandStatus.failed
    assert "отменено" in cancelled.result

    # Повторная отмена — уже не в ожидании.
    _, ok2, _ = await commands_domain.cancel_armed(
        session, car_id=car.id, command_id=command.id
    )
    assert ok2 is False

    # После отмены срабатывание не должно произойти даже на остановке.
    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()
    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 0
    assert adapter_ok == []


async def test_adapter_error_keeps_block_armed_for_retry(session, monkeypatch):
    """Временный сбой адаптера не хоронит взвод — пробуем на следующей точке."""
    from app.clients.adapter import AdapterError

    async def _boom(external_id, command, params=None):
        raise AdapterError("адаптер недоступен")

    monkeypatch.setattr(commands_domain, "send_command", _boom)

    car, _ = await _car(session, speed=20.0, motion=True)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()

    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()

    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 0
    refreshed = await session.get(Command, command.id)
    assert refreshed.status is CommandStatus.armed
    assert "повтор" in refreshed.result

    from app.domain import alerts as alerts_domain

    assert await alerts_domain.list_alerts(session, status="open") == []


async def test_terminal_failure_raises_alert(session, monkeypatch):
    """Адаптер отверг команду — админ должен узнать, а не остаться с тихим failed."""
    async def _reject(external_id, command, params=None):
        return {"status": "failed", "result": "устройство не ответило"}

    monkeypatch.setattr(commands_domain, "send_command", _reject)

    car, _ = await _car(session, speed=20.0, motion=True)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()
    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()

    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 0
    assert (await session.get(Command, command.id)).status is CommandStatus.failed

    from app.domain import alerts as alerts_domain

    alerts = await alerts_domain.list_alerts(session, status="open")
    assert [a.type.value for a in alerts] == ["armed_block_failed"]


async def test_unblock_cancels_armed_block(session, adapter_ok):
    """Разблокировка снимает взвод — иначе машина заглушится вопреки воле админа."""
    car, _ = await _car(session, speed=20.0, motion=True)
    block, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()
    assert block.status is CommandStatus.armed

    await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_unblock", requested_by=111
    )
    await session.commit()
    assert (await session.get(Command, block.id)).status is CommandStatus.failed

    # Машина встала — блокировки быть не должно.
    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()
    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 0
    assert ("9175358042", "engine_stop") not in adapter_ok


async def test_fire_armed_sends_once_on_repeat(session, adapter_ok):
    """Повторный проход не шлёт команду второй раз (захват снял её с armed)."""
    car, _ = await _car(session, speed=20.0, motion=True)
    await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()
    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()

    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 1
    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 0
    assert adapter_ok == [("9175358042", "engine_stop")]


async def test_non_adapter_send_error_reverts_to_armed(session, monkeypatch):
    """Не-AdapterError (напр. не-JSON ответ) не должен оставить команду в queued."""
    async def _bad(external_id, command, params=None):
        raise ValueError("ответ адаптера не JSON")

    monkeypatch.setattr(commands_domain, "send_command", _bad)

    car, _ = await _car(session, speed=20.0, motion=True)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    await session.commit()
    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()

    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 0
    refreshed = await session.get(Command, command.id)
    assert refreshed.status is CommandStatus.armed, "в queued застрять нельзя"
    assert "повтор" in refreshed.result

    from app.domain import alerts as alerts_domain

    assert await alerts_domain.list_alerts(session, status="open") == []


async def test_sweep_recovers_stuck_queued_claim(session, adapter_ok):
    """Зависший захват (queued, старый updated_at) sweep возвращает в armed."""
    car, _ = await _car(session)
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    cmd = Command(
        car_id=car.id,
        type=CommandType.engine_stop,
        status=CommandStatus.queued,
        requested_by=111,
        created_at=old,
        updated_at=old,  # момент захвата давно прошёл — точно завис
    )
    session.add(cmd)
    await session.commit()

    await commands_domain.sweep_unconfirmed(session, now=datetime.now(timezone.utc))

    assert (await session.get(Command, cmd.id)).status is CommandStatus.armed


async def test_sweep_keeps_fresh_claim(session, adapter_ok):
    """Свежий захват (старый created_at, но updated_at только что) НЕ трогаем —
    отправка может ещё идти, иначе вернулась бы двойная отправка."""
    car, _ = await _car(session)
    cmd = Command(
        car_id=car.id,
        type=CommandType.engine_stop,
        status=CommandStatus.queued,
        requested_by=111,
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),  # взведено давно
        updated_at=datetime.now(timezone.utc),  # но захвачено только что
    )
    session.add(cmd)
    await session.commit()

    await commands_domain.sweep_unconfirmed(session, now=datetime.now(timezone.utc))

    assert (await session.get(Command, cmd.id)).status is CommandStatus.queued


async def test_fired_armed_block_confirms_not_falsely_unconfirmed(session, adapter_ok):
    """Взвод, ждавший дольше окна подтверждения, после отправки подтверждается
    телеметрией и не ловит ложный command_unconfirmed (окно мерим от отправки)."""
    car, _ = await _car(session, speed=20.0, motion=True)
    command, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True,
    )
    command.created_at = datetime.now(timezone.utc) - timedelta(hours=1)  # нажато давно
    await session.commit()

    state = await session.get(CarState, car.id)
    state.speed_knots = 0.0
    state.motion = False
    state.ignition = False
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()
    assert await commands_domain.fire_armed(session, car_ids=[car.id]) == 1

    # Sweep сразу после отправки НЕ должен пометить unconfirmed.
    assert await commands_domain.sweep_unconfirmed(
        session, now=datetime.now(timezone.utc)
    ) == 0
    assert (await session.get(Command, command.id)).status is CommandStatus.sent

    from app.domain import alerts as alerts_domain

    assert [a.type.value for a in await alerts_domain.list_alerts(session, status="open")] == [
        "armed_block_fired"
    ]

    # Телеметрия подтверждает блокировку (точка ПОСЛЕ отправки) → acked.
    state = await session.get(CarState, car.id)
    state.engine_blocked = True
    state.last_ts = datetime.now(timezone.utc)
    await session.commit()
    assert await commands_domain.confirm_by_telemetry(session) == 1
    assert (await session.get(Command, command.id)).status is CommandStatus.acked


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
