"""Расписания фоновых задач: домен, валидация периода и HTTP."""
from __future__ import annotations

import pytest
from app.db.models import PeriodicTask, TaskRun, TaskRunStatus
from app.domain import periodic as periodic_service
from app.errors import Conflict, Validation
from app.main import app
from app.tasks.beat import parse_crontab

if not any(getattr(r, "path", None) == "/periodic-tasks" for r in app.routes):
    raise AssertionError("роутер periodic должен подключаться в app/main.py")

TASK = "app.tasks.ping.ping"


async def _make(session, **kw):
    params = {"name": "проверка штрафов", "task": TASK, "interval_seconds": 3600}
    params.update(kw)
    return await periodic_service.create_task(session, **params)


# --- домен -----------------------------------------------------------------


async def test_create_reads_back_with_defaults(session):
    row = await _make(session)

    assert row.enabled is True
    assert row.total_run_count == 0
    assert row.last_run_at is None


async def test_period_must_be_exactly_one_of_two(session):
    """Заданы оба или ни одного — непонятно, какой период главный."""
    with pytest.raises(Validation):
        await _make(session, interval_seconds=3600, crontab="0 9 * * *")
    with pytest.raises(Validation):
        await _make(session, interval_seconds=None, crontab=None)


async def test_too_frequent_interval_rejected(session):
    """Проверка парка занимает минуты — секундный интервал бессмыслен."""
    with pytest.raises(Validation):
        await _make(session, interval_seconds=5)


async def test_broken_crontab_rejected(session):
    with pytest.raises(Validation):
        await _make(session, interval_seconds=None, crontab="каждое утро")


async def test_duplicate_name_is_conflict(session):
    await _make(session)
    with pytest.raises(Conflict):
        await _make(session)


async def test_update_revalidates_period(session):
    """Правка в админке не должна оставить расписание без периода."""
    row = await _make(session)

    with pytest.raises(Validation):
        await periodic_service.update_task(session, row.id, crontab="0 9 * * *")


async def test_update_switches_interval_to_crontab(session):
    row = await _make(session)

    updated = await periodic_service.update_task(
        session, row.id, interval_seconds=None, crontab="0 9 * * *"
    )

    assert updated.interval_seconds is None
    assert updated.crontab == "0 9 * * *"


async def test_delete_removes(session):
    row = await _make(session)

    assert await periodic_service.delete_task(session, row.id) is True
    assert await periodic_service.get_task(session, row.id) is None


# --- журнал прогонов --------------------------------------------------------

async def _run(session, status, task=TASK):
    run = TaskRun(task=task, status=status)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


# --- планировщик ------------------------------------------------------------


def test_crontab_from_table_is_understood_by_celery():
    parsed = parse_crontab("0 9 * * 1-5")

    assert parsed.hour == {9}
    assert parsed.day_of_week == {1, 2, 3, 4, 5}


# --- HTTP -------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/periodic-tasks"),
        ("post", "/periodic-tasks"),
        ("patch", "/periodic-tasks/1"),
        ("delete", "/periodic-tasks/1"),
        ("get", "/task-runs"),
    ],
)
async def test_endpoints_require_admin(client, method, path):
    """Забытый Depends на одном маршруте иначе не поймается."""
    resp = await getattr(client, method)(path)

    assert resp.status_code in (401, 403)


async def test_crud_over_http(admin_client):
    created = await admin_client.post(
        "/periodic-tasks",
        json={"name": "штрафы", "task": TASK, "crontab": "0 9 * * *"},
    )
    assert created.status_code == 201
    task_id = created.json()["id"]

    listed = await admin_client.get("/periodic-tasks")
    assert [t["name"] for t in listed.json()] == ["штрафы"]

    patched = await admin_client.patch(
        f"/periodic-tasks/{task_id}", json={"enabled": False}
    )
    assert patched.json()["enabled"] is False
    assert patched.json()["crontab"] == "0 9 * * *", "частичная правка не трогает период"

    assert (await admin_client.delete(f"/periodic-tasks/{task_id}")).status_code == 204


async def test_bad_period_over_http_is_422(admin_client):
    resp = await admin_client.post(
        "/periodic-tasks",
        json={"name": "битое", "task": TASK, "interval_seconds": 3600, "crontab": "0 9 * * *"},
    )

    assert resp.status_code == 422


async def test_runs_endpoint_filters_by_task(admin_client, session):
    await _run(session, TaskRunStatus.refused)
    await _run(session, TaskRunStatus.ok, task="other.task")

    resp = await admin_client.get("/task-runs", params={"task": TASK})

    assert [r["status"] for r in resp.json()] == ["refused"]


async def test_rename_to_taken_name_is_conflict_not_500(admin_client, session):
    """Гонку и занятое имя ловит БД: SELECT перед вставкой её пропускает."""
    await _make(session, name="первое")
    second = await _make(session, name="второе")
    second_id = second.id  # запрос идёт в своей сессии, объект просрочится

    resp = await admin_client.patch(
        f"/periodic-tasks/{second_id}", json={"name": "первое"}
    )

    assert resp.status_code == 409


async def test_duplicate_name_over_http_is_conflict(admin_client, session):
    await _make(session, name="занято")

    resp = await admin_client.post(
        "/periodic-tasks",
        json={"name": "занято", "task": TASK, "interval_seconds": 3600},
    )

    assert resp.status_code == 409


async def test_unknown_field_in_patch_is_ignored_not_silently_applied(admin_client, session):
    row = await _make(session, name="расписание")
    row_id = row.id

    resp = await admin_client.patch(
        f"/periodic-tasks/{row_id}", json={"enabled": False, "неизвестное": 1}
    )

    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


# --- планировщик: расписание переживает пересборку ---------------------------


def _scheduler_with(rows):
    """Планировщик поверх подставленной таблицы: БД для этого не нужна."""
    from app.tasks.beat import DatabaseScheduler
    from app.tasks.celery_app import celery_app

    class Fake(DatabaseScheduler):
        def _rows(self):
            return rows

    return Fake(app=celery_app, lazy=True)


def test_entry_keeps_last_run_from_db():
    """Иначе пересборка расписания каждые 30 с обнуляет таймер.

    Суточная задача при этом не запустилась бы никогда: до следующего срока
    остаётся сутки, а отсчёт начинается заново на каждой пересборке.
    """
    from datetime import datetime, timedelta, timezone

    ran_at = datetime.now(timezone.utc) - timedelta(hours=20)
    row = PeriodicTask(
        id=1, name="штрафы", task=TASK, crontab=None,
        interval_seconds=86400, args=None, enabled=True, last_run_at=ran_at,
    )

    entries = _scheduler_with([row])._build()

    assert entries["штрафы"].last_run_at == ran_at


def test_naive_last_run_is_made_aware():
    """SQLite отдаёт naive datetime, а celery сравнивает с aware."""
    from datetime import datetime

    row = PeriodicTask(
        id=1, name="штрафы", task=TASK, crontab=None,
        interval_seconds=3600, args=None, enabled=True,
        last_run_at=datetime(2026, 9, 6, 8, 0),
    )

    entry = _scheduler_with([row])._build()["штрафы"]

    assert entry.last_run_at.tzinfo is not None


def test_task_due_after_interval_elapsed():
    from datetime import datetime, timedelta, timezone

    row = PeriodicTask(
        id=1, name="штрафы", task=TASK, crontab=None,
        interval_seconds=3600, args=None, enabled=True,
        last_run_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    entry = _scheduler_with([row])._build()["штрафы"]

    assert entry.is_due().is_due is True


def test_broken_schedule_does_not_break_the_rest():
    """Одно битое расписание не должно ронять весь beat."""
    broken = PeriodicTask(
        id=1, name="битое", task=TASK, crontab="каждое утро",
        interval_seconds=None, args=None, enabled=True, last_run_at=None,
    )
    good = PeriodicTask(
        id=2, name="живое", task=TASK, crontab=None,
        interval_seconds=3600, args=None, enabled=True, last_run_at=None,
    )

    entries = _scheduler_with([broken, good])._build()

    assert list(entries) == ["живое"]
