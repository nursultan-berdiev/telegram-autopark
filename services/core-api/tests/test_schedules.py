"""Тесты графиков платежей: периодичность, частичная оплата, просрочка (Этап 3 + §92)."""
from datetime import datetime, timezone

from app.db.models import SchedulePeriod
from app.domain import cars as cars_service
from app.domain import drivers as drivers_service
from app.domain import schedules as sched

UTC = timezone.utc


def test_advance_due_date():
    d = datetime(2024, 1, 31, tzinfo=UTC)
    assert sched.advance_due_date(d, SchedulePeriod.daily, None).day == 1
    assert sched.advance_due_date(d, SchedulePeriod.weekly, None) == datetime(
        2024, 2, 7, tzinfo=UTC
    )
    # 31 янв + месяц → 29 фев (високосный, корректировка конца месяца)
    assert sched.advance_due_date(d, SchedulePeriod.monthly, None) == datetime(
        2024, 2, 29, tzinfo=UTC
    )
    # 10 дней без воскресений 04.02 и 11.02
    assert sched.advance_due_date(d, SchedulePeriod.custom, 10) == datetime(
        2024, 2, 12, tzinfo=UTC
    )


def test_add_months_over_year():
    assert sched.add_months(
        datetime(2024, 12, 15, tzinfo=UTC), 1
    ) == datetime(2025, 1, 15, tzinfo=UTC)


async def _schedule(session, *, period, amount, next_due, interval_days=None, plate="AA"):
    car = await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )
    driver = await drivers_service.register_driver(
        session, tg_user_id=1, full_name="П", phone="+1", inn="11111111",
        selfie_file_id=None, selfie_path=None, car_id=car.id,
    )
    return await sched.set_schedule(
        session, driver_id=driver.id, period=period, interval_days=interval_days,
        amount=amount, next_due_date=next_due,
    )


async def test_full_payment_advances_one_period(session):
    s = await _schedule(
        session, period=SchedulePeriod.weekly, amount=1500.0,
        next_due=datetime(2024, 6, 1, tzinfo=UTC),
    )
    res = await sched.apply_payment(session, s, 1500.0)
    assert res.periods_closed == 1
    assert float(res.paid_in_period) == 0.0
    assert s.next_due_date.replace(tzinfo=None) == datetime(2024, 6, 8)


async def test_partial_payment_holds_due_date(session):
    s = await _schedule(
        session, period=SchedulePeriod.weekly, amount=1500.0,
        next_due=datetime(2024, 6, 1, tzinfo=UTC),
    )
    res = await sched.apply_payment(session, s, 500.0)
    assert res.periods_closed == 0
    assert float(res.paid_in_period) == 500.0
    assert float(res.remaining_current) == 1000.0
    # срок не сдвинулся — период ещё не закрыт
    assert s.next_due_date.replace(tzinfo=None) == datetime(2024, 6, 1)

    # добор до полной суммы закрывает период и двигает срок
    res2 = await sched.apply_payment(session, s, 1000.0)
    assert res2.periods_closed == 1
    assert float(res2.paid_in_period) == 0.0
    assert s.next_due_date.replace(tzinfo=None) == datetime(2024, 6, 8)


async def test_overpayment_closes_multiple_periods(session):
    s = await _schedule(
        session, period=SchedulePeriod.weekly, amount=1000.0,
        next_due=datetime(2024, 6, 1, tzinfo=UTC),
    )
    res = await sched.apply_payment(session, s, 2500.0)
    assert res.periods_closed == 2
    assert float(res.paid_in_period) == 500.0  # предоплата третьего периода
    assert s.next_due_date.replace(tzinfo=None) == datetime(2024, 6, 15)


async def test_set_schedule_resets_partial(session):
    s = await _schedule(
        session, period=SchedulePeriod.weekly, amount=1500.0,
        next_due=datetime(2024, 6, 1, tzinfo=UTC),
    )
    await sched.apply_payment(session, s, 500.0)
    assert float(s.paid_in_period) == 500.0
    # новые условия обнуляют накопленную частичную оплату
    s2 = await sched.set_schedule(
        session, driver_id=s.driver_id, period=SchedulePeriod.monthly,
        interval_days=None, amount=2000.0, next_due_date=datetime(2024, 7, 1, tzinfo=UTC),
    )
    assert s2.id == s.id and float(s2.paid_in_period) == 0.0


async def test_status_not_due(session):
    s = await _schedule(
        session, period=SchedulePeriod.monthly, amount=1500.0,
        next_due=datetime(2024, 2, 1, tzinfo=UTC),
    )
    st = sched.schedule_status(s, now=datetime(2024, 1, 20, tzinfo=UTC))
    assert st.is_overdue is False and st.overdue_periods == 0
    assert float(st.debt_now) == 0.0
    assert float(st.remaining_current) == 1500.0


async def test_status_overdue_single_period(session):
    s = await _schedule(
        session, period=SchedulePeriod.monthly, amount=1500.0,
        next_due=datetime(2024, 1, 10, tzinfo=UTC),
    )
    st = sched.schedule_status(s, now=datetime(2024, 1, 15, tzinfo=UTC))
    assert st.is_overdue and st.overdue_periods == 1
    assert st.overdue_days == 4  # 5 календарных минус воскресенье 14.01
    assert float(st.debt_now) == 1500.0


async def test_status_overdue_with_partial(session):
    s = await _schedule(
        session, period=SchedulePeriod.monthly, amount=1500.0,
        next_due=datetime(2024, 1, 10, tzinfo=UTC),
    )
    await sched.apply_payment(session, s, 500.0)  # частичная, срок не сдвинулся
    st = sched.schedule_status(s, now=datetime(2024, 1, 15, tzinfo=UTC))
    assert st.is_overdue and st.overdue_periods == 1
    assert float(st.debt_now) == 1000.0  # остаток текущего периода


async def test_status_multi_period_overdue(session):
    s = await _schedule(
        session, period=SchedulePeriod.monthly, amount=1000.0,
        next_due=datetime(2024, 1, 10, tzinfo=UTC),
    )
    # 10 янв, 10 фев, 10 мар наступили к 15 марта → 3 периода
    st = sched.schedule_status(s, now=datetime(2024, 3, 15, tzinfo=UTC))
    assert st.overdue_periods == 3
    assert float(st.debt_now) == 3000.0  # остаток(1000) + 2 полных периода


# ----------------------------------------------- статус сразу после сохранения (PJ-13, п.5)
def _sched(days_offset: int, amount: float = 1000.0):
    from datetime import datetime, timedelta, timezone

    from app.db.models import PaymentSchedule, SchedulePeriod

    return PaymentSchedule(
        driver_id=1,
        period=SchedulePeriod.weekly,
        interval_days=None,
        amount=amount,
        paid_in_period=0,
        next_due_date=datetime.now(timezone.utc) + timedelta(days=days_offset),
        active=True,
    )




async def test_overpaid_inactive_schedule_has_no_negative_remainder(session):
    """Неактивный график только копит: переплата не должна давать «остаток -500»."""
    from decimal import Decimal

    schedule = await _schedule(
        session,
        period=SchedulePeriod.weekly,
        amount=1000.0,
        next_due=datetime(2024, 6, 1, tzinfo=UTC),
        plate="ZZ",
    )
    schedule.active = False
    await session.commit()

    applied = await sched.apply_payment(session, schedule, Decimal("1500.00"))
    st = sched.schedule_status(schedule)

    assert applied.remaining_current == Decimal("0.00")
    assert st.remaining_current == Decimal("0.00")
    assert st.debt_now >= Decimal("0.00")
    assert "-" not in sched.due_summary(st)


# ------------------------------------------------ воскресенье — выходной
# Октябрь 2026: пт 02, сб 03, вс 04, пн 05, вт 06. Срок — 00:00 UTC (06:00 в
# Бишкеке), «сейчас» — 04:00 UTC (10:00 в Бишкеке).
def _oct(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def test_daily_and_custom_skip_sunday():
    assert sched.advance_due_date(_oct(3), SchedulePeriod.daily, None) == _oct(5)
    # 3 дня от пятницы: сб, пн, вт
    assert sched.advance_due_date(_oct(2), SchedulePeriod.custom, 3) == _oct(6)


async def test_paid_through_saturday_nothing_due_on_sunday(session):
    s = await _schedule(
        session, period=SchedulePeriod.daily, amount=1500.0, next_due=_oct(3)
    )
    res = await sched.apply_payment(session, s, 1500.0)
    assert res.next_due_date == _oct(5)

    sunday = sched.schedule_status(s, now=_oct(4, 4))
    assert not sunday.is_overdue and float(sunday.debt_now) == 0.0

    monday = sched.schedule_status(s, now=_oct(5, 4))
    assert monday.overdue_periods == 1 and monday.overdue_days == 0
    assert float(monday.debt_now) == 1500.0
    assert sched.due_summary(monday) == "срок сегодня, к оплате 1500.00"


async def test_unpaid_saturday_sunday_adds_no_debt_or_days(session):
    s = await _schedule(
        session, period=SchedulePeriod.daily, amount=1500.0, next_due=_oct(3)
    )
    expected = {  # день: (дней просрочки, периодов, долг)
        4: (1, 1, 1500.0),
        5: (1, 2, 3000.0),
        6: (2, 3, 4500.0),
    }
    for day, (days, periods, debt) in expected.items():
        st = sched.schedule_status(s, now=_oct(day, 4))
        assert (st.overdue_days, st.overdue_periods, float(st.debt_now)) == (
            days, periods, debt,
        ), day


async def test_daily_start_on_sunday_pays_monday_once(session):
    s = await _schedule(
        session, period=SchedulePeriod.daily, amount=1500.0, next_due=_oct(4)
    )
    st = sched.schedule_status(s, now=_oct(4, 4))
    assert st.next_due_date == _oct(5) and not st.is_overdue

    res = await sched.apply_payment(session, s, 1500.0)
    assert res.next_due_date == _oct(6)


async def test_weekly_due_on_sunday_is_due_monday(session):
    s = await _schedule(
        session, period=SchedulePeriod.weekly, amount=9000.0, next_due=_oct(4)
    )
    st = sched.schedule_status(s, now=_oct(5, 4))
    assert st.next_due_date == _oct(5)
    assert st.overdue_periods == 1 and st.overdue_days == 0


async def test_monthly_sunday_shift_keeps_day_of_month(session):
    s = await _schedule(
        session, period=SchedulePeriod.monthly, amount=30000.0, next_due=_oct(4)
    )
    assert sched.schedule_status(s, now=_oct(1)).next_due_date == _oct(5)

    res = await sched.apply_payment(session, s, 30000.0)
    # от плановой 04.10, а не от фактической 05.10
    assert res.next_due_date == datetime(2026, 11, 4, tzinfo=UTC)
