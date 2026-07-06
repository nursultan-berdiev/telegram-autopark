"""Тесты графиков платежей (Этап 3)."""
from datetime import datetime, timezone

from bot.db.models import SchedulePeriod
from bot.services import cars as cars_service
from bot.services import drivers as drivers_service
from bot.services import schedules as sched


def test_advance_due_date():
    d = datetime(2024, 1, 31, tzinfo=timezone.utc)
    assert sched.advance_due_date(d, SchedulePeriod.daily, None).day == 1
    assert sched.advance_due_date(d, SchedulePeriod.weekly, None) == datetime(
        2024, 2, 7, tzinfo=timezone.utc
    )
    # 31 янв + месяц → 29 фев (високосный, корректировка конца месяца)
    assert sched.advance_due_date(d, SchedulePeriod.monthly, None) == datetime(
        2024, 2, 29, tzinfo=timezone.utc
    )
    assert sched.advance_due_date(d, SchedulePeriod.custom, 10) == datetime(
        2024, 2, 10, tzinfo=timezone.utc
    )


def test_add_months_over_year():
    assert sched.add_months(
        datetime(2024, 12, 15, tzinfo=timezone.utc), 1
    ) == datetime(2025, 1, 15, tzinfo=timezone.utc)


async def test_set_and_bump(session):
    car = await cars_service.create_car(
        session, plate="AA", model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=1, full_name="П", phone="+1", inn="11111111",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    s1 = await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.weekly,
        interval_days=None, amount=1500.0, next_due_date=start,
    )
    # повторный вызов обновляет тот же график
    s2 = await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.monthly,
        interval_days=None, amount=2000.0, next_due_date=start,
    )
    assert s2.id == s1.id and s2.period == SchedulePeriod.monthly

    await sched.bump_after_payment(session, s2)
    assert s2.next_due_date.replace(tzinfo=None) == datetime(2024, 7, 1)
