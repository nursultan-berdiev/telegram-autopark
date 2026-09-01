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
    assert sched.advance_due_date(d, SchedulePeriod.custom, 10) == datetime(
        2024, 2, 10, tzinfo=UTC
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
    assert st.overdue_days == 5
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
