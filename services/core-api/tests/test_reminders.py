"""Тесты ежедневных напоминаний: кого дёргаем, когда и не чаще раза в день."""
from datetime import date, datetime, timezone

from app.db.models import SchedulePeriod
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import reminders as rem
from app.domain import schedules as sched

TZ = "Asia/Bishkek"  # UTC+6
UTC = timezone.utc


async def _driver_with_schedule(session, *, next_due, tg_id=1, plate="AA", amount=1500.0):
    car = await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=tg_id, full_name=f"Водитель {tg_id}", phone="+1",
        inn="11111111", selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    schedule = await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.weekly,
        interval_days=None, amount=amount, next_due_date=next_due,
    )
    return driver, schedule


async def test_reminds_on_due_day(session):
    # срок 10.07 00:00 UTC; «сейчас» 10.07 09:00 по Бишкеку (03:00 UTC)
    await _driver_with_schedule(session, next_due=datetime(2026, 7, 10, tzinfo=UTC))
    plan = await rem.collect(session, datetime(2026, 7, 10, 3, tzinfo=UTC), TZ)

    assert len(plan.reminders) == 1
    r = plan.reminders[0]
    assert r.kind == rem.KIND_DUE_TODAY
    assert "Сегодня срок платежа" in r.text and "1500.00" in r.text


async def test_reminds_day_before(session):
    # срок 11.07, «сейчас» 10.07 → напоминаем «завтра»
    await _driver_with_schedule(session, next_due=datetime(2026, 7, 11, tzinfo=UTC))
    plan = await rem.collect(session, datetime(2026, 7, 10, 3, tzinfo=UTC), TZ)

    assert len(plan.reminders) == 1
    assert plan.reminders[0].kind == rem.KIND_TOMORROW
    assert "завтра" in plan.reminders[0].text


async def test_reminds_overdue_with_debt(session):
    # срок был 05.07, «сейчас» 10.07 → просрочка, долг за пропущенные периоды
    await _driver_with_schedule(session, next_due=datetime(2026, 7, 5, tzinfo=UTC))
    plan = await rem.collect(session, datetime(2026, 7, 10, 3, tzinfo=UTC), TZ)

    assert len(plan.reminders) == 1
    r = plan.reminders[0]
    assert r.kind == rem.KIND_OVERDUE
    assert "Просрочка 5 дн." in r.text
    assert plan.total_debt > 0


async def test_silent_when_due_far_away(session):
    # срок через неделю — молчим
    await _driver_with_schedule(session, next_due=datetime(2026, 7, 20, tzinfo=UTC))
    plan = await rem.collect(session, datetime(2026, 7, 10, 3, tzinfo=UTC), TZ)

    assert plan.reminders == []
    assert plan.owner_digest() is None


async def test_no_double_reminder_same_day(session):
    """Анти-спам: при просрочке в N дней водитель получает 1 сообщение в день."""
    _, schedule = await _driver_with_schedule(
        session, next_due=datetime(2026, 7, 5, tzinfo=UTC)
    )
    now = datetime(2026, 7, 10, 3, tzinfo=UTC)

    plan = await rem.collect(session, now, TZ)
    assert len(plan.reminders) == 1

    await rem.mark_reminded(session, [schedule.id], date(2026, 7, 10))

    again = await rem.collect(session, now, TZ)
    assert again.reminders == [], "второй раз за день напоминать нельзя"
    # но во владельческой сводке водитель остаётся — она шлётся раз в день
    assert again.owner_lines


async def test_force_ignores_antispam(session):
    """/remind_now force — чтобы QA мог перепроверить рассылку в тот же день."""
    _, schedule = await _driver_with_schedule(
        session, next_due=datetime(2026, 7, 5, tzinfo=UTC)
    )
    now = datetime(2026, 7, 10, 3, tzinfo=UTC)
    await rem.mark_reminded(session, [schedule.id], date(2026, 7, 10))

    assert (await rem.collect(session, now, TZ)).reminders == []
    forced = await rem.collect(session, now, TZ, force=True)
    assert len(forced.reminders) == 1


async def test_owner_digest_lists_everyone(session):
    await _driver_with_schedule(
        session, next_due=datetime(2026, 7, 5, tzinfo=UTC), tg_id=1, plate="AA"
    )
    await _driver_with_schedule(
        session, next_due=datetime(2026, 7, 10, tzinfo=UTC), tg_id=2, plate="BB"
    )
    plan = await rem.collect(session, datetime(2026, 7, 10, 3, tzinfo=UTC), TZ)

    digest = plan.owner_digest()
    assert digest is not None
    assert "Водитель 1" in digest and "Водитель 2" in digest
    assert "просрочен 5 дн." in digest and "срок сегодня" in digest
    assert "Суммарный долг" in digest


async def test_timezone_boundary(session):
    """Локальный день считается по TZ парка, а не по UTC.

    01:00 UTC 11.07 — это уже 07:00 11.07 в Бишкеке, значит срок 11.07 = сегодня.
    """
    await _driver_with_schedule(session, next_due=datetime(2026, 7, 11, tzinfo=UTC))
    plan = await rem.collect(session, datetime(2026, 7, 11, 1, tzinfo=UTC), TZ)

    assert plan.reminders[0].kind == rem.KIND_DUE_TODAY
