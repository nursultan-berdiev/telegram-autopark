"""Авто-блокировка за неоплату и авто-разблокировка при оплате (домен).

Системный блок помечен commands.source='overdue' — на этом держится и единый
платёжный алерт, и защита от снятия ручного блока админа.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    AlertStatus,
    AlertType,
    Car,
    CarState,
    Command,
    CommandSource,
    CommandStatus,
    CommandType,
    SchedulePeriod,
    Tracker,
    TrackerProvider,
)
from app.domain import alerts as alerts_domain
from app.domain import commands as commands_domain
from app.domain import drivers as drivers_service
from app.domain import overdue_enforcement as enforcement
from app.domain import schedules as sched
from app.domain.overdue_enforcement import BlockOutcome, ReleaseOutcome

# Фиксированная среда (2026-10-07, будний): воскресный сдвиг срока не мешает тесту.
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def adapter_ok(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def _send(external_id: str, command: str, params=None):
        sent.append((external_id, command))
        return {"status": "sent", "result": "S20,OK"}

    monkeypatch.setattr(commands_domain, "send_command", _send)
    return sent


async def _car(
    session,
    *,
    plate="01KG555AAA",
    external_id="9175358042",
    speed=0.0,
    ignition=False,
    motion=False,
    engine_blocked=False,
):
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
            car_id=car.id,
            tracker_id=tracker.id,
            last_ts=NOW - timedelta(seconds=10),
            speed_knots=speed,
            ignition=ignition,
            motion=motion,
            engine_blocked=engine_blocked,
        )
    )
    await session.commit()
    return car, tracker


async def _driver(session, car_id, *, tg_user_id, overdue):
    driver = await drivers_service.register_driver(
        session,
        tg_user_id=tg_user_id,
        full_name="Иванов",
        phone="+1",
        inn=str(tg_user_id),
        selfie_file_id=None,
        selfie_path=None,
        car_id=car_id,
    )
    due = NOW - timedelta(days=1) if overdue else NOW + timedelta(days=2)
    await sched.set_schedule(
        session,
        driver_id=driver.id,
        period=SchedulePeriod.daily,
        interval_days=None,
        amount=1800.0,
        next_due_date=due,
    )
    await session.commit()
    return driver


async def _system_block(session, car, tracker, *, status, source=CommandSource.overdue, requested_by=None):
    cmd = Command(
        car_id=car.id, tracker_id=tracker.id, type=CommandType.engine_stop,
        status=status, source=source, requested_by=requested_by,
        created_at=NOW, updated_at=NOW,
    )
    session.add(cmd)
    await session.commit()
    return cmd


async def _open_types(session):
    alerts = await alerts_domain.list_alerts(session, status="open")
    return [a.type.value for a in alerts]


# --- always_arm / enforce ---------------------------------------------------


async def test_always_arm_arms_even_parked(session, adapter_ok):
    car, _ = await _car(session)
    command, ok, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block",
        requested_by=None, source=CommandSource.overdue, always_arm=True, now=NOW,
    )
    await session.commit()
    assert ok is False
    assert command.status is CommandStatus.armed
    assert command.source == CommandSource.overdue
    assert adapter_ok == [], "на реле сразу ничего не ушло"


async def test_enforce_overdue_block_arms_system_command(session, adapter_ok):
    car, _ = await _car(session)
    await _driver(session, car.id, tg_user_id=1, overdue=True)

    assert await enforcement.enforce_overdue_block(session, car_id=car.id, now=NOW) == BlockOutcome.armed
    await session.commit()

    cmd = await session.scalar(select(Command).where(Command.car_id == car.id))
    assert cmd.status is CommandStatus.armed
    assert cmd.source == "overdue" and cmd.requested_by is None
    assert adapter_ok == []


async def test_enforce_skips_already_blocked(session, adapter_ok):
    car, _ = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=True)
    assert await enforcement.enforce_overdue_block(session, car_id=car.id, now=NOW) == BlockOutcome.skipped_blocked


async def test_enforce_skips_inflight(session, adapter_ok):
    car, _ = await _car(session)
    await _driver(session, car.id, tg_user_id=1, overdue=True)
    assert await enforcement.enforce_overdue_block(session, car_id=car.id, now=NOW) == BlockOutcome.armed
    await session.commit()
    assert await enforcement.enforce_overdue_block(session, car_id=car.id, now=NOW) == BlockOutcome.skipped_inflight


async def test_overdue_car_ids_respects_active_driver_and_schedule(session, adapter_ok):
    car, _ = await _car(session)
    await _driver(session, car.id, tg_user_id=1, overdue=True)
    assert await enforcement.overdue_car_ids(session, NOW) == {car.id}
    # Не просроченный — не в наборе.
    car2, _ = await _car(session, plate="BB", external_id="222")
    await _driver(session, car2.id, tg_user_id=2, overdue=False)
    assert await enforcement.overdue_car_ids(session, NOW) == {car.id}


# --- fire_armed: тип алерта по источнику ------------------------------------


async def test_enforce_no_tracker_is_reported(session, adapter_ok):
    """Машина без трекера: блок не создать — отдельный исход, не падение."""
    car = Car(plate="01KG000AAA")
    session.add(car)
    await session.flush()
    session.add(CarState(car_id=car.id, last_ts=NOW - timedelta(seconds=10)))
    await _driver(session, car.id, tg_user_id=1, overdue=True)

    assert await enforcement.enforce_overdue_block(session, car_id=car.id, now=NOW) == BlockOutcome.no_tracker
    assert adapter_ok == []


async def test_release_skips_when_resume_already_sent(session, adapter_ok):
    """Resume уже отправлен (последняя значимая команда) → блок не активен,
    повторный resume и дубль уведомления не шлём (закрывает повтор при unconfirmed)."""
    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    await _system_block(session, car, tracker, status=CommandStatus.acked)
    # Разблокировка уже ушла ранее (ещё не подтверждена телеметрией).
    session.add(Command(
        car_id=car.id, tracker_id=tracker.id, type=CommandType.engine_resume,
        status=CommandStatus.sent, source=CommandSource.overdue, created_at=NOW, updated_at=NOW,
    ))
    await session.commit()

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.no_system_block
    assert adapter_ok == [], "второй resume не отправляли"
    assert "overdue_unblock" not in await _open_types(session)
    assert car.id not in await enforcement.cars_under_system_block(session), "уже не кандидат"


async def test_fire_armed_system_block_raises_overdue_alert(session, adapter_ok):
    car, _ = await _car(session)
    await _driver(session, car.id, tg_user_id=1, overdue=True)
    await enforcement.enforce_overdue_block(session, car_id=car.id, now=NOW)
    await session.commit()

    assert await commands_domain.fire_armed(session, car_ids=[car.id], now=NOW) == 1
    assert adapter_ok == [("9175358042", "engine_stop")]
    assert await _open_types(session) == ["overdue_block_fired"]


async def test_fire_armed_admin_block_keeps_armed_alert(session, adapter_ok):
    car, _ = await _car(session, speed=20.0, motion=True)
    await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block",
        requested_by=111, arm_if_unsafe=True, now=NOW,
    )
    await session.commit()
    state = await session.get(CarState, car.id)
    state.speed_knots, state.motion, state.ignition = 0.0, False, False
    state.last_ts = NOW - timedelta(seconds=5)
    await session.commit()

    assert await commands_domain.fire_armed(session, car_ids=[car.id], now=NOW) == 1
    assert await _open_types(session) == ["armed_block_fired"]


# --- release_if_paid --------------------------------------------------------


async def test_release_unblocks_paid_system_block(session, adapter_ok):
    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    await _system_block(session, car, tracker, status=CommandStatus.acked)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.unblocked
    await session.commit()
    assert ("9175358042", "engine_resume") in adapter_ok
    assert "overdue_unblock" in await _open_types(session)


async def test_release_keeps_block_if_still_overdue(session, adapter_ok):
    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=True)
    await _system_block(session, car, tracker, status=CommandStatus.acked)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.still_overdue
    assert adapter_ok == []


async def test_release_keeps_block_when_no_driver(session, adapter_ok):
    """Нет активного водителя/графика → «неизвестно», блок НЕ снимаем молча."""
    car, tracker = await _car(session, engine_blocked=True)
    await _system_block(session, car, tracker, status=CommandStatus.acked)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.no_driver
    assert adapter_ok == []


async def test_release_ignores_manual_block(session, adapter_ok):
    """Ручной блок админа (source=manual) авто-разблокировка не снимает."""
    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    await _system_block(session, car, tracker, status=CommandStatus.acked, source=CommandSource.manual, requested_by=111)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.no_system_block
    assert adapter_ok == []


async def test_release_ignores_system_block_revived_by_later_manual(session, adapter_ok):
    """Старый системный acked + позже ручной блок админа → авто-разблокировка
    НЕ снимает ручной блок (ищем ПОСЛЕДНЮЮ значимую команду, а не любую системную)."""
    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    await _system_block(session, car, tracker, status=CommandStatus.acked)  # старый системный
    # Позже админ заблокировал вручную (новая команда с бо́льшим id).
    await _system_block(session, car, tracker, status=CommandStatus.acked, source=CommandSource.manual, requested_by=111)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.no_system_block
    assert adapter_ok == [], "ручной блок админа не снимаем"
    assert car.id not in await enforcement.cars_under_system_block(session)


async def test_release_disarms_paid_armed_block(session, adapter_ok):
    car, tracker = await _car(session)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    armed = await _system_block(session, car, tracker, status=CommandStatus.armed)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.disarmed
    await session.commit()
    assert (await session.get(Command, armed.id)).status is CommandStatus.failed
    assert adapter_ok == [], "взвод сняли без отправки resume"


async def test_release_reports_failure_without_false_unblock(session, adapter_ok, monkeypatch):
    """Resume не ушёл на реле → не врём «разблокировано», поднимаем _failed."""
    async def _fail(external_id, command, params=None):
        return {"status": "failed", "result": "адаптер отклонил"}

    monkeypatch.setattr(commands_domain, "send_command", _fail)

    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    await _system_block(session, car, tracker, status=CommandStatus.acked)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.unblock_failed
    await session.commit()
    types = await _open_types(session)
    assert "overdue_unblock_failed" in types
    assert "overdue_unblock" not in types, "ложного «разблокировано» быть не должно"


async def test_manual_block_over_system_is_not_auto_released(session, adapter_ok):
    """Админ жмёт «Заблокировать» поверх системного взвода (из меню, без alert_id)
    → блок становится его, и авто-разблокировка после оплаты его НЕ снимает."""
    car, tracker = await _car(session)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    sys_cmd = await _system_block(session, car, tracker, status=CommandStatus.armed)
    # Ручной блок из меню (без alert_id) — забирает системную команду себе (дедуп).
    cmd, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111, now=NOW,
    )
    await session.commit()
    assert cmd.id == sys_cmd.id and cmd.source == CommandSource.manual, "системный блок стал ручным"

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.no_system_block


async def test_unblock_resolves_block_alert_so_next_cycle_notifies(session, adapter_ok):
    """Второй цикл «блок→оплата»: новый overdue_block_fired должен доставиться,
    а не схлопнуться со старым открытым (у того notified_at уже стоит)."""
    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    await _system_block(session, car, tracker, status=CommandStatus.acked)
    # Первый цикл уже доставил «заблокирован».
    a1 = await alerts_domain.raise_alert(
        session, car_id=car.id, atype=AlertType.overdue_block_fired, payload={}, text="заблокирован"
    )
    await alerts_domain.mark_notified(session, a1)
    await session.commit()

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.unblocked
    await session.commit()

    assert a1.status is AlertStatus.resolved, "старый «заблокирован» закрыт"
    unblock = [a for a in await alerts_domain.list_alerts(session, status="open") if a.type.value == "overdue_unblock"]
    assert len(unblock) == 1 and unblock[0].notified_at is None, "«разблокировано» доставляемо"

    # Новый блок следующего цикла поднимается как новый, а не обновляет старый.
    a2 = await alerts_domain.raise_alert(
        session, car_id=car.id, atype=AlertType.overdue_block_fired, payload={}, text="снова"
    )
    await session.commit()
    assert a2.id != a1.id and a2.notified_at is None


async def test_manual_card_block_takes_over_system_arm(session, adapter_ok):
    """Ручной блок с карточки алерта (alert_id задан) — отдельная команда с
    бо́льшим id. Системный взвод при этом забирается в source=manual, так что
    авто-разблокировка его не снимет и оплатившего не заглушит."""
    car, tracker = await _car(session, speed=20.0, motion=True)  # едет → новый блок armed
    await _driver(session, car.id, tg_user_id=1, overdue=False)  # оплатил
    sys_cmd = await _system_block(session, car, tracker, status=CommandStatus.armed)
    alert = await alerts_domain.raise_alert(
        session, car_id=car.id, atype=AlertType.overdue_payment, payload={}, text="просрочка"
    )
    await session.commit()
    manual, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        alert_id=alert.id, arm_if_unsafe=True, now=NOW,
    )
    await session.commit()
    assert manual.id != sys_cmd.id and manual.source == CommandSource.manual
    # Прежний системный взвод переведён в ручной — больше не системный.
    assert (await session.get(Command, sys_cmd.id)).source == CommandSource.manual

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.no_system_block
    assert adapter_ok == [], "ни один блок не сняли"
    assert (await session.get(Command, manual.id)).status is CommandStatus.armed


async def test_release_handles_unconfirmed_system_block(session, adapter_ok):
    """sweep перевёл блок в unconfirmed, телеметрия подтвердила engine_blocked —
    машину всё равно надо уметь разблокировать."""
    car, tracker = await _car(session, engine_blocked=True)
    await _driver(session, car.id, tg_user_id=1, overdue=False)
    await _system_block(session, car, tracker, status=CommandStatus.unconfirmed)

    assert await enforcement.release_if_paid(session, car_id=car.id, now=NOW) == ReleaseOutcome.unblocked
    await session.commit()
    assert ("9175358042", "engine_resume") in adapter_ok


async def test_cars_under_system_block_lists_candidates(session, adapter_ok):
    car1, tr1 = await _car(session, plate="AA", external_id="111")
    car2, tr2 = await _car(session, plate="BB", external_id="222")
    car3, tr3 = await _car(session, plate="CC", external_id="333")
    await _system_block(session, car1, tr1, status=CommandStatus.armed)
    await _system_block(session, car2, tr2, status=CommandStatus.acked, source=CommandSource.manual, requested_by=111)
    # car3: системный блок уже снят более поздним resume → не кандидат (нет вечных acked).
    await _system_block(session, car3, tr3, status=CommandStatus.acked)
    session.add(Command(
        car_id=car3.id, tracker_id=tr3.id, type=CommandType.engine_resume,
        status=CommandStatus.acked, source=CommandSource.overdue, created_at=NOW, updated_at=NOW,
    ))
    await session.commit()

    ids = await enforcement.cars_under_system_block(session)
    assert car1.id in ids, "взведённый системный — кандидат"
    assert car2.id not in ids, "ручной блок не кандидат"
    assert car3.id not in ids, "снятый resume блок не кандидат"


async def test_enforce_rechecks_overdue_under_lock(session, adapter_ok):
    """Кандидат взят в начале прогона, но водитель успел оплатить — под локом
    перепроверяем и блок не ставим."""
    car, _ = await _car(session)
    await _driver(session, car.id, tg_user_id=1, overdue=False)  # уже оплатил

    assert await enforcement.enforce_overdue_block(session, car_id=car.id, now=NOW) == BlockOutcome.not_overdue
    assert await session.scalar(select(Command).where(Command.car_id == car.id)) is None


async def test_system_resume_keeps_manual_arm(session, adapter_ok):
    """Системный resume снимает только свой взвод; ручной отложенный блок админа
    (угон: «заглушить на остановке») остаётся."""
    car, tracker = await _car(session, speed=20.0, motion=True)
    manual, _, _ = await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_block", requested_by=111,
        arm_if_unsafe=True, now=NOW,  # source=manual по умолчанию
    )
    await session.commit()
    assert manual.status is CommandStatus.armed and manual.source == CommandSource.manual

    # Системный resume (как из release_if_paid).
    await commands_domain.request_command(
        session, car_id=car.id, type_value="engine_unblock",
        requested_by=None, source=CommandSource.overdue, now=NOW,
    )
    await session.commit()
    assert (await session.get(Command, manual.id)).status is CommandStatus.armed, "ручной взвод не трогаем"
