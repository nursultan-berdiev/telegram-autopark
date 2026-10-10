"""Вечерние уведомления у срока 22:00: предупреждение, «блок включён», дайджест."""
from datetime import date, datetime, timedelta, timezone

from app.db.models import (
    Car,
    Command,
    CommandStatus,
    CommandType,
    Driver,
    Payment,
    PaymentSchedule,
    PaymentStatus,
    SchedulePeriod,
)
from app.domain import payments as pay
from app.domain import reminders

UTC = timezone.utc


async def _driver_with_schedule(
    session,
    *,
    plate,
    next_due,
    amount=1800,
    paid=0,
    tg=None,
    warned=None,
    block_notice=None,
    full_name=None,
    active=True,
    with_car=True,
):
    car = None
    car_id = None
    if with_car:
        car = Car(plate=plate, model="Sonata")
        session.add(car)
        await session.flush()
        car_id = car.id
    driver = Driver(
        tg_user_id=tg if tg is not None else (hash(plate) % 10**9),
        full_name=full_name or f"Водитель {plate}",
        phone="+1",
        inn=plate,
        car_id=car_id,
        active=active,
    )
    session.add(driver)
    await session.flush()
    sched = PaymentSchedule(
        driver_id=driver.id,
        period=SchedulePeriod.daily,
        interval_days=None,
        amount=amount,
        paid_in_period=paid,
        next_due_date=next_due,
        active=True,
        last_warned_on=warned,
        last_block_notice_on=block_notice,
    )
    session.add(sched)
    await session.commit()
    return car, driver, sched


async def test_collect_overdue_warning_window(session):
    # Среда 14.10.2026, 21:45 — срок 22:00.
    now = datetime(2026, 10, 14, 21, 45, tzinfo=UTC)
    await _driver_with_schedule(session, plate="A", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC), tg=1)  # через 15 мин
    await _driver_with_schedule(session, plate="B", next_due=datetime(2026, 10, 14, 23, 45, tzinfo=UTC), tg=2)  # через 2 ч
    await _driver_with_schedule(session, plate="C", next_due=datetime(2026, 10, 14, 20, 0, tzinfo=UTC), tg=3)  # уже просрочен
    await _driver_with_schedule(session, plate="D", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC), paid=1800, tg=4)  # оплачен
    await _driver_with_schedule(session, plate="E", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC), warned=date(2026, 10, 14), tg=5)  # уже предупреждён

    rows = await reminders.collect_overdue_warning(session, now, "UTC", lead_minutes=20)
    assert {r.tg_user_id for r in rows} == {1}
    assert "A" in rows[0].text and "22:00" in rows[0].text


async def test_collect_overdue_warning_force_ignores_mark(session):
    now = datetime(2026, 10, 14, 21, 45, tzinfo=UTC)
    await _driver_with_schedule(session, plate="E", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC), warned=date(2026, 10, 14), tg=5)
    rows = await reminders.collect_overdue_warning(session, now, "UTC", lead_minutes=20, force=True)
    assert {r.tg_user_id for r in rows} == {5}


async def test_collect_block_notice(session):
    now = datetime(2026, 10, 14, 22, 0, tzinfo=UTC)
    await _driver_with_schedule(session, plate="A", next_due=datetime(2026, 10, 14, 21, 0, tzinfo=UTC), tg=1)  # просрочен
    await _driver_with_schedule(session, plate="B", next_due=datetime(2026, 10, 15, 22, 0, tzinfo=UTC), tg=2)  # срок завтра
    await _driver_with_schedule(session, plate="C", next_due=datetime(2026, 10, 14, 21, 0, tzinfo=UTC), block_notice=date(2026, 10, 14), tg=3)  # уже уведомлён

    rows = await reminders.collect_block_notice(session, now, "UTC")
    assert {r.tg_user_id for r in rows} == {1}
    assert "включена" in rows[0].text


async def test_mark_warned_and_block_notice(session):
    _, _, sched = await _driver_with_schedule(session, plate="A", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC))
    today = date(2026, 10, 14)
    await reminders.mark_warned(session, [sched.id], today)
    await reminders.mark_block_notice(session, [sched.id], today)
    await session.refresh(sched)
    assert sched.last_warned_on == today and sched.last_block_notice_on == today


async def test_paid_today(session):
    car, driver, _ = await _driver_with_schedule(session, plate="A", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC))
    now = datetime.now(UTC)
    session.add(Payment(driver_id=driver.id, car_id=car.id, amount=1800, status=PaymentStatus.confirmed))  # сегодня (default now)
    session.add(Payment(driver_id=driver.id, car_id=car.id, amount=900, status=PaymentStatus.confirmed, created_at=now - timedelta(days=2)))  # позавчера
    await session.commit()

    rows = await pay.paid_today(session, "UTC")
    assert len(rows) == 1
    did, name, plate, total = rows[0]
    assert did == driver.id and plate == "A" and total == 1800.0


async def test_admin_digest_buckets(session):
    now = datetime.now(UTC)
    # Оплатил сегодня.
    car_a, drv_a, _ = await _driver_with_schedule(session, plate="A", next_due=now + timedelta(days=1), tg=1)
    session.add(Payment(driver_id=drv_a.id, car_id=car_a.id, amount=1800, status=PaymentStatus.confirmed))
    # Просрочен.
    await _driver_with_schedule(session, plate="B", next_due=now - timedelta(days=1), tg=2)
    # Машина под авто-блоком за аренду.
    car_c, _, _ = await _driver_with_schedule(session, plate="C", next_due=now - timedelta(days=1), tg=3)
    session.add(Command(car_id=car_c.id, type=CommandType.engine_stop, status=CommandStatus.acked, source="overdue", created_at=now, updated_at=now))
    await session.commit()

    text = await reminders.collect_admin_digest(session, now, "UTC")
    assert "Оплатили сегодня" in text and "A" in text
    assert "просрочено" in text and "B" in text
    assert "Автоблокировка" in text and "C" in text and "за аренду" in text


async def test_admin_digest_empty_states(session):
    now = datetime.now(UTC)
    # Один оплативший вперёд (срок завтра, остаток 0) → не просрочен, без блока.
    await _driver_with_schedule(session, plate="A", next_due=now + timedelta(days=1), paid=1800, tg=1)
    text = await reminders.collect_admin_digest(session, now, "UTC")
    assert "все оплатили" in text and "Автоблокировок нет" in text


async def test_block_notice_skips_inactive_and_carless(session):
    """Blocking-замечание: уведомляем ровно тех, кого реально блокирует задача —
    активных водителей с машиной (как в overdue_car_ids)."""
    now = datetime(2026, 10, 14, 22, 0, tzinfo=UTC)
    overdue = datetime(2026, 10, 14, 21, 0, tzinfo=UTC)
    await _driver_with_schedule(session, plate="OK", next_due=overdue, tg=1)  # норм
    await _driver_with_schedule(session, plate="OFF", next_due=overdue, tg=2, active=False)  # неактивен
    await _driver_with_schedule(session, plate="NOCAR", next_due=overdue, tg=3, with_car=False)  # без машины

    rows = await reminders.collect_block_notice(session, now, "UTC")
    assert {r.tg_user_id for r in rows} == {1}
    # То же для утреннего плана и предупреждения — единый отбор.
    warn = await reminders.collect_overdue_warning(
        session, datetime(2026, 10, 14, 21, 45, tzinfo=UTC), "UTC", lead_minutes=20
    )
    assert all(r.tg_user_id != 2 and r.tg_user_id != 3 for r in warn)


async def test_admin_digest_escapes_user_values(session):
    """Имя водителя с '<'/'&' не должно ломать HTML-сообщение — экранируем."""
    now = datetime.now(UTC)
    await _driver_with_schedule(
        session, plate="B", next_due=now - timedelta(days=1), tg=1, full_name="Иван <b>&"
    )
    text = await reminders.collect_admin_digest(session, now, "UTC")
    assert "Иван <b>&" not in text
    assert "&lt;b&gt;" in text and "&amp;" in text


async def test_warn_text_uses_schedule_due_time(session):
    """Время блокировки в тексте берётся из графика, а не из константы."""
    now = datetime(2026, 10, 14, 21, 45, tzinfo=UTC)
    await _driver_with_schedule(session, plate="A", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC), tg=1)
    rows = await reminders.collect_overdue_warning(session, now, "UTC", lead_minutes=20)
    assert "В 22:00 двигатель будет заблокирован" in rows[0].text


async def test_paid_today_respects_now_and_status(session):
    from app.db.models import PaymentStatus

    car, driver, _ = await _driver_with_schedule(session, plate="A", next_due=datetime(2026, 10, 14, 22, 0, tzinfo=UTC))
    # «Сейчас» для сводки — фиксированный момент; оплата в этот день и днём раньше.
    now = datetime(2026, 10, 14, 23, 0, tzinfo=UTC)
    session.add(Payment(driver_id=driver.id, car_id=car.id, amount=1800, status=PaymentStatus.confirmed, created_at=datetime(2026, 10, 14, 9, 0, tzinfo=UTC)))
    session.add(Payment(driver_id=driver.id, car_id=car.id, amount=500, status=PaymentStatus.confirmed, created_at=datetime(2026, 10, 13, 9, 0, tzinfo=UTC)))
    await session.commit()
    rows = await pay.paid_today(session, "UTC", now=now)
    assert len(rows) == 1 and rows[0][3] == 1800.0
