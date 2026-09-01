"""Эвалуаторы правил, дедуп алертов и авто-resolve."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import (
    AlertStatus,
    AlertType,
    Car,
    CarState,
    Driver,
    Fine,
    FineStatus,
    MaintenanceItem,
    PaymentSchedule,
    Rule,
    RuleType,
    SchedulePeriod,
    Tracker,
    TrackerProvider,
)
from app.domain import alerts as alerts_domain
from app.rules import engine


async def _car(session, plate="01KG111AAA"):
    car = Car(plate=plate)
    session.add(car)
    await session.flush()
    return car


async def _overdue_driver(session, car, *, days=20):
    driver = Driver(
        tg_user_id=900 + car.id,
        full_name="Водитель",
        phone="+996",
        inn="1",
        car_id=car.id,
        active=True,
    )
    session.add(driver)
    await session.flush()
    session.add(
        PaymentSchedule(
            driver_id=driver.id,
            period=SchedulePeriod.weekly,
            amount=Decimal("1000.00"),
            paid_in_period=Decimal("0.00"),
            next_due_date=datetime.now(timezone.utc) - timedelta(days=days),
            active=True,
        )
    )
    await session.flush()
    return driver


async def test_overdue_rule_raises_alert_once(session):
    car = await _car(session)
    await _overdue_driver(session, car)
    rule = Rule(type=RuleType.overdue_payment, params={"min_days": 1})
    session.add(rule)
    await session.commit()

    await engine.evaluate_all(session)
    await engine.evaluate_all(session)

    alerts = await alerts_domain.list_alerts(session, status="open")
    assert len(alerts) == 1
    assert alerts[0].type is AlertType.overdue_payment
    assert alerts[0].payload["overdue_days"] >= 20


async def test_alert_auto_resolves_when_paid(session):
    car = await _car(session)
    driver = await _overdue_driver(session, car)
    rule = Rule(type=RuleType.overdue_payment, params={"min_days": 1})
    session.add(rule)
    await session.commit()
    await engine.evaluate_all(session)

    row = await session.scalar(
        select(PaymentSchedule).where(PaymentSchedule.driver_id == driver.id)
    )
    row.next_due_date = datetime.now(timezone.utc) + timedelta(days=7)
    await session.commit()

    await engine.evaluate_all(session)

    open_alerts = await alerts_domain.list_alerts(session, status="open")
    resolved = await alerts_domain.list_alerts(session, status="resolved")
    assert open_alerts == []
    assert len(resolved) == 1
    assert resolved[0].status is AlertStatus.resolved


async def test_fines_rule_counts_only_unpaid(session):
    car = await _car(session)
    now = datetime.now(timezone.utc)
    for _ in range(3):
        session.add(Fine(car_id=car.id, issued_at=now, status=FineStatus.unpaid))
    rule = Rule(type=RuleType.fines_count, params={"count": 3})
    session.add(rule)
    await session.commit()

    await engine.evaluate_all(session)
    assert len(await alerts_domain.list_alerts(session, status="open")) == 1

    paid = await session.scalar(select(Fine).limit(1))
    paid.status = FineStatus.paid
    await session.commit()

    await engine.evaluate_all(session)
    assert await alerts_domain.list_alerts(session, status="open") == []


async def test_maintenance_rule_needs_trusted_odometer(session):
    car = await _car(session)
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id="9175358042"
    )
    session.add(tracker)
    await session.flush()
    session.add(
        CarState(
            car_id=car.id,
            tracker_id=tracker.id,
            last_ts=datetime.now(timezone.utc),
            odometer_km=Decimal("15000"),
            odometer_tracker_id=tracker.id,
            odometer_trusted=False,
        )
    )
    session.add(
        MaintenanceItem(
            car_id=car.id,
            type="oil",
            interval_km=Decimal("10000"),
            last_service_km=Decimal("0"),
            last_service_tracker_id=tracker.id,
        )
    )
    rule = Rule(type=RuleType.maintenance_km, params={"grace_km": 0})
    session.add(rule)
    await session.commit()

    await engine.evaluate_all(session)

    types = {a.type for a in await alerts_domain.list_alerts(session, status="open")}
    assert AlertType.maintenance_km not in types, "по недостоверному пробегу ТО не считаем"
    assert AlertType.odometer_untrusted in types, "но и молчать нельзя"


async def test_maintenance_rule_fires_on_trusted_odometer(session):
    car = await _car(session)
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id="9175358042"
    )
    session.add(tracker)
    await session.flush()
    session.add(
        CarState(
            car_id=car.id,
            tracker_id=tracker.id,
            last_ts=datetime.now(timezone.utc),
            odometer_km=Decimal("15000"),
            odometer_tracker_id=tracker.id,
            odometer_trusted=True,
        )
    )
    session.add(
        MaintenanceItem(
            car_id=car.id,
            type="oil",
            interval_km=Decimal("10000"),
            last_service_km=Decimal("0"),
            last_service_tracker_id=tracker.id,
        )
    )
    rule = Rule(type=RuleType.maintenance_km, params={"grace_km": 0})
    session.add(rule)
    await session.commit()

    await engine.evaluate_all(session)

    alerts = await alerts_domain.list_alerts(session, status="open")
    assert [a.type for a in alerts] == [AlertType.maintenance_km]
    assert Decimal(alerts[0].payload["over_km"]) == Decimal("5000")


async def test_untrusted_odometer_silent_without_maintenance(session):
    """Без записей ТО алерт о базе пробега — чистый шум."""
    car = await _car(session)
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id="9175358042"
    )
    session.add(tracker)
    await session.flush()
    session.add(
        CarState(
            car_id=car.id,
            tracker_id=tracker.id,
            last_ts=datetime.now(timezone.utc),
            odometer_km=Decimal("100"),
            odometer_tracker_id=tracker.id,
            odometer_trusted=False,
        )
    )
    await session.commit()

    await engine.check_odometer_trust(session)
    await session.commit()

    assert await alerts_domain.list_alerts(session, status="open") == []


async def test_system_alert_updates_payload_not_triggered_at(session):
    """Повтор не плодит второй open и не двигает время первого срабатывания."""
    car = await _car(session)
    first = datetime.now(timezone.utc) - timedelta(hours=5)
    await alerts_domain.raise_alert(
        session,
        car_id=car.id,
        atype=AlertType.command_unconfirmed,
        payload={"command_id": 1},
        text="первый",
        now=first,
    )
    await session.commit()

    later = datetime.now(timezone.utc)
    await alerts_domain.raise_alert(
        session,
        car_id=car.id,
        atype=AlertType.command_unconfirmed,
        payload={"command_id": 2},
        text="второй",
        now=later,
    )
    await session.commit()

    alerts = await alerts_domain.list_alerts(session, status="open")
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.payload["command_id"] == 2, "снимок обновился"
    assert alert.triggered_at.replace(tzinfo=timezone.utc) == first.replace(microsecond=alert.triggered_at.microsecond)
    assert alert.last_seen_at is not None


async def test_maintenance_reports_most_overdue_item(session):
    """Из нескольких просроченных ТО показываем самое просроченное, а не случайное."""
    car = await _car(session, plate="01KG808AAA")
    tracker = Tracker(
        car_id=car.id, provider=TrackerProvider.traccar, external_id="9175358042"
    )
    session.add(tracker)
    await session.flush()
    session.add(
        CarState(
            car_id=car.id,
            tracker_id=tracker.id,
            last_ts=datetime.now(timezone.utc),
            odometer_km=Decimal("30000"),
            odometer_tracker_id=tracker.id,
            odometer_trusted=True,
        )
    )
    for mtype, interval in (("oil", Decimal("10000")), ("filter", Decimal("25000"))):
        session.add(
            MaintenanceItem(
                car_id=car.id,
                type=mtype,
                interval_km=interval,
                last_service_km=Decimal("0"),
                last_service_tracker_id=tracker.id,
            )
        )
    session.add(Rule(type=RuleType.maintenance_km, params={"grace_km": 0}))
    await session.commit()

    await engine.evaluate_all(session)

    alert = (await alerts_domain.list_alerts(session, status="open"))[0]
    assert alert.payload["type"] == "oil", "перепробег по маслу больше"
    assert alert.payload["overdue_items"] == 2
