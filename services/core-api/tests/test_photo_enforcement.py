"""Задел авто-блокировки за пропуск фотоконтроля: политика на общем ядре.

Задача не подключена к celery (см. test_photo_inspections), но механика должна
работать уже сейчас — включение не должно стать первым её прогоном.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import (
    AlertType,
    Car,
    CarState,
    Command,
    CommandSource,
    CommandStatus,
    Driver,
    PhotoSchedule,
    Tracker,
    TrackerProvider,
)
from app.domain import alerts as alerts_domain
from app.domain import commands as commands_domain
from app.domain import engine_enforcement as enforcement
from app.domain import photo_inspections as photo
from app.domain.engine_enforcement import BlockOutcome, ReleaseOutcome
from app.tasks import photo_enforcement

NOW = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 17)
POLICY = enforcement.photo_policy(2)
PHOTOS = {
    "front_file_id": "F",
    "left_file_id": "L",
    "rear_file_id": "R",
    "right_file_id": "RT",
    "interior_front_file_id": "IF",
    "interior_rear_file_id": "IR",
}


@pytest.fixture(autouse=True)
def adapter_ok(monkeypatch):
    async def _send(external_id, command, params=None):
        return {"status": "sent", "result": "S20,OK"}

    monkeypatch.setattr(commands_domain, "send_command", _send)


async def _car_with_driver(
    session, *, overdue_days: int, plate="01KG555AAA", tg=501, today: date = TODAY
):
    car = Car(plate=plate)
    session.add(car)
    await session.flush()
    tracker = Tracker(car_id=car.id, provider=TrackerProvider.traccar, external_id=plate)
    session.add(tracker)
    await session.flush()
    session.add(
        CarState(
            car_id=car.id, tracker_id=tracker.id, last_ts=NOW, speed_knots=0.0,
            ignition=False, motion=False,
        )
    )
    driver = Driver(tg_user_id=tg, full_name="Вод", phone="+1", inn=str(tg), car_id=car.id)
    session.add(driver)
    await session.flush()
    session.add(PhotoSchedule(driver_id=driver.id, next_due_on=today - timedelta(days=overdue_days)))
    await session.commit()
    return car


async def test_policy_applies_from_n_days(session):
    two = await _car_with_driver(session, overdue_days=2, plate="AA", tg=1)
    one = await _car_with_driver(session, overdue_days=1, plate="BB", tg=2)
    free = Car(plate="FREE")
    session.add(free)
    await session.commit()

    assert await POLICY.applies(session, two.id, NOW) is True
    assert await POLICY.applies(session, one.id, NOW) is False
    # Нет водителя — «нет данных»: не блокируем, а уже стоящий блок держим.
    assert await POLICY.applies(session, free.id, NOW) is None


async def test_block_fires_with_photo_alert_and_releases_after_submit(session):
    car = await _car_with_driver(session, overdue_days=3)

    assert await enforcement.enforce_block(session, car_id=car.id, policy=POLICY, now=NOW) is (
        BlockOutcome.armed
    )
    await session.commit()
    command = await session.scalar(select(Command).where(Command.car_id == car.id))
    assert command.source == CommandSource.photo and command.status is CommandStatus.armed

    assert await commands_domain.fire_armed(session, car_ids=[car.id], now=NOW) == 1
    fired = [a.type for a in await alerts_domain.list_alerts(session, status="open")]
    assert AlertType.photo_block_fired in fired

    await photo.submit(
        session, tg_user_id=501, has_issues=False, comment=None, now=NOW, **PHOTOS
    )
    outcome = await enforcement.release_if_cleared(session, car_id=car.id, policy=POLICY, now=NOW)
    assert outcome is ReleaseOutcome.unblocked
    opened = [a.type for a in await alerts_domain.list_alerts(session, status="open")]
    assert AlertType.photo_unblock in opened and AlertType.photo_block_fired not in opened


async def test_overdue_release_does_not_touch_photo_block(session):
    car = await _car_with_driver(session, overdue_days=3)
    await enforcement.enforce_block(session, car_id=car.id, policy=POLICY, now=NOW)
    await session.commit()

    assert await enforcement.cars_under_system_block(session, CommandSource.overdue) == []
    assert await enforcement.cars_under_system_block(session, CommandSource.photo) == [car.id]


def _scope_over(session):
    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Scope()


async def test_task_does_nothing_without_kill_switch(session, monkeypatch):
    """Даже подключённая к воркеру по ошибке задача без выключателя не глушит машины."""
    monkeypatch.setattr(photo_enforcement, "session_scope", _scope_over(session))
    await _car_with_driver(session, overdue_days=10)

    assert await photo_enforcement._run(2) == {"disabled": 1}
    assert list(await session.scalars(select(Command))) == []


async def test_task_rejects_zero_threshold(monkeypatch):
    monkeypatch.setattr(settings, "photo_autoblock_enabled", True)

    with pytest.raises(ValueError):
        await photo_enforcement._run(0)


async def test_task_arms_only_long_overdue(session, monkeypatch):
    monkeypatch.setattr(settings, "photo_autoblock_enabled", True)
    monkeypatch.setattr(photo_enforcement, "session_scope", _scope_over(session))
    # Задача берёт настоящее «сейчас» — срок считаем от него, иначе тест зависел бы от даты.
    today = photo.local_today(datetime.now(timezone.utc))
    late = await _car_with_driver(session, overdue_days=2, plate="AA", tg=1, today=today)
    await _car_with_driver(session, overdue_days=1, plate="BB", tg=2, today=today)

    outcomes = await photo_enforcement._run(2)

    # Только что взведённый блок проход снятия видит и держит: условие в силе.
    assert outcomes == {"block-armed": 1, "release-still-applies": 1}
    (command,) = list(await session.scalars(select(Command)))
    assert command.car_id == late.id and command.source == CommandSource.photo
