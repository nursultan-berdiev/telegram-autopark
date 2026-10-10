"""Celery-задачи уведомлений: считают доменом и пишут в очередь outbound_messages."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.db.models import Car, Driver, PaymentSchedule, SchedulePeriod
from app.domain import outbox
from app.routers.admin import KNOWN_TASKS
from app.tasks import notifications

UTC = timezone.utc


def _scope_over(session):
    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Scope()


def _scope_rollback(session):
    """Как настоящий session_scope: незакоммиченное теряется на выходе. Ловит
    забытый commit (иначе фейк без отката скрыл бы потерю строк)."""
    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            await session.rollback()
            return False

    return lambda: _Scope()


async def _driver(session, *, plate, next_due, tg, paid=0, amount=1800):
    car = Car(plate=plate, model="Sonata")
    session.add(car)
    await session.flush()
    driver = Driver(
        tg_user_id=tg, full_name=f"Вод {plate}", phone="+1", inn=plate,
        car_id=car.id, active=True,
    )
    session.add(driver)
    await session.flush()
    session.add(PaymentSchedule(
        driver_id=driver.id, period=SchedulePeriod.daily, interval_days=None,
        amount=amount, paid_in_period=paid, next_due_date=next_due, active=True,
    ))
    await session.commit()
    return car, driver


async def test_block_notice_task_enqueues_overdue(session, monkeypatch):
    monkeypatch.setattr(notifications, "session_scope", _scope_over(session))
    await _driver(session, plate="A", next_due=datetime(2020, 1, 1, tzinfo=UTC), tg=10)

    out = await notifications._run_driver_notice("block_notice")
    rows = await outbox.list_pending(session)
    assert out["drivers"] == 1
    assert len(rows) == 1
    assert rows[0].recipient_tg_user_id == 10 and rows[0].kind == "block_notice"
    assert "включена" in rows[0].text

    # Повторный прогон за тот же день — дедуп, новых строк нет.
    out2 = await notifications._run_driver_notice("block_notice")
    assert out2.get("drivers", 0) == 0
    assert len(await outbox.list_pending(session)) == 1


async def test_warn_task_enqueues_due_soon(session, monkeypatch):
    monkeypatch.setattr(notifications, "session_scope", _scope_over(session))
    soon = datetime.now(UTC) + timedelta(minutes=10)
    await _driver(session, plate="B", next_due=soon, tg=20)

    out = await notifications._run_driver_notice("warn")
    rows = await outbox.list_pending(session)
    assert out["drivers"] == 1
    assert rows[0].recipient_tg_user_id == 20 and rows[0].kind == "warn"


async def test_digest_task_enqueues_to_each_admin(session, monkeypatch):
    monkeypatch.setattr(notifications, "session_scope", _scope_over(session))
    await _driver(session, plate="C", next_due=datetime(2020, 1, 1, tzinfo=UTC), tg=30)

    out = await notifications._run_digest()
    rows = await outbox.list_pending(session)
    # Админов двое (ADMIN_IDS=111,222 в conftest).
    assert out["owners"] == 2
    assert {r.recipient_tg_user_id for r in rows} == {111, 222}
    assert all(r.kind == "digest" for r in rows)
    assert "Платежи" in rows[0].text


async def test_daily_task_enqueues_owner_digest(session, monkeypatch):
    monkeypatch.setattr(notifications, "session_scope", _scope_over(session))
    await _driver(session, plate="D", next_due=datetime(2020, 1, 1, tzinfo=UTC), tg=40)

    out = await notifications._run_daily()
    rows = await outbox.list_pending(session)
    # Сводка владельцу идёт в любой день (включая воскресенье); адресатов двое.
    assert out["owners"] == 2
    admin_rows = [r for r in rows if r.recipient_tg_user_id in (111, 222)]
    assert len(admin_rows) == 2 and all(r.kind == "digest" for r in admin_rows)


async def test_daily_digest_persists_without_driver_reminders(session, monkeypatch):
    """Сводка владельцу не теряется, когда водительских напоминаний нет (их
    дедуп уже сработал): _run_daily обязан сделать явный commit."""
    monkeypatch.setattr(notifications, "session_scope", _scope_rollback(session))
    _, driver = await _driver(session, plate="E", next_due=datetime(2020, 1, 1, tzinfo=UTC), tg=50)
    sched = await session.scalar(
        select(PaymentSchedule).where(PaymentSchedule.driver_id == driver.id)
    )
    # «Уже напоминали сегодня» → в plan.reminders пусто, owner_lines есть.
    sched.last_reminded_on = datetime.now(ZoneInfo(settings.timezone)).date()
    await session.commit()

    out = await notifications._run_daily()
    rows = await outbox.list_pending(session)
    admin_rows = [r for r in rows if r.recipient_tg_user_id in (111, 222)]
    assert out["owners"] == 2 and len(admin_rows) == 2  # пережили rollback → commit был


def test_notification_tasks_registered_in_known_tasks():
    for name in (notifications.DAILY, notifications.WARN, notifications.BLOCK, notifications.DIGEST):
        assert name in KNOWN_TASKS
