"""Расписания фоновых задач: домен, валидация периода и HTTP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


async def test_catalog_lists_known_tasks(admin_client):
    resp = await admin_client.get("/periodic-tasks/catalog")

    assert resp.status_code == 200
    body = resp.json()
    assert TASK in body and body[TASK], "каталог отдаёт имя задачи и её подпись"


async def test_create_rejects_unknown_task(admin_client):
    """Имя вне каталога — запуск произвольной точки входа из браузера (422 как Validation)."""
    resp = await admin_client.post(
        "/periodic-tasks",
        json={"name": "чужая", "task": "os.system", "interval_seconds": 3600},
    )

    assert resp.status_code == 422


async def test_patch_rejects_unknown_task(admin_client, session):
    row = await _make(session, name="живая")

    resp = await admin_client.patch(f"/periodic-tasks/{row.id}", json={"task": "os.system"})

    assert resp.status_code == 422


def _capture_send(monkeypatch) -> list:
    from app.tasks import celery_app as celery_module

    calls: list = []
    monkeypatch.setattr(
        celery_module.celery_app, "send_task", lambda name, **kw: calls.append((name, kw))
    )
    return calls


async def test_run_now_enqueues_with_run_id_and_args(admin_client, session, monkeypatch):
    calls = _capture_send(monkeypatch)
    row = await _make(session, name="ручной", args={"mode": "full"})

    resp = await admin_client.post(f"/periodic-tasks/{row.id}/run")

    assert resp.status_code == 202
    assert resp.json()["task"] == TASK
    assert len(calls) == 1
    name, kw = calls[0]
    assert name == TASK
    assert kw["retry"] is False
    # kwargs как у beat (args + periodic_task_id) ПЛЮС run_id для привязки строки.
    assert kw["kwargs"]["mode"] == "full"
    assert kw["kwargs"]["periodic_task_id"] == row.id
    assert isinstance(kw["kwargs"]["run_id"], int)
    # Прогон заведён до постановки и помечен, кем запрошен (actor из X-TG-User-Id).
    run = await periodic_service.running_task(session, TASK)
    assert run is not None and run.requested_by == 111


async def test_run_now_rejects_task_outside_catalog(admin_client, session, monkeypatch):
    """Строку с произвольным task, заведённую в обход роутера, «Запустить» не пустит."""
    calls = _capture_send(monkeypatch)
    row = PeriodicTask(name="левая", task="os.system", interval_seconds=3600, enabled=True)
    session.add(row)
    await session.commit()

    resp = await admin_client.post(f"/periodic-tasks/{row.id}/run")

    assert resp.status_code == 422
    assert calls == []  # в очередь ничего не ушло


async def test_run_now_dedup_second_click(admin_client, session, monkeypatch):
    calls = _capture_send(monkeypatch)
    row = await _make(session, name="двойной клик")

    first = await admin_client.post(f"/periodic-tasks/{row.id}/run")
    second = await admin_client.post(f"/periodic-tasks/{row.id}/run")

    assert first.status_code == 202 and second.status_code == 202
    assert len(calls) == 1, "пока прогон не завершён, второй раз в очередь не ставим"


async def test_run_now_missing_is_404(admin_client):
    assert (await admin_client.post("/periodic-tasks/9999/run")).status_code == 404


async def test_run_now_broker_down_is_503_and_closes_run(admin_client, session, monkeypatch):
    from app.tasks import celery_app as celery_module

    def boom(*a, **k):
        raise OSError("брокер недоступен")

    monkeypatch.setattr(celery_module.celery_app, "send_task", boom)
    row = await _make(session, name="без очереди")

    assert (await admin_client.post(f"/periodic-tasks/{row.id}/run")).status_code == 503
    # Строку прогона закрыли — иначе уникальный индекс навсегда заблокировал бы запуск.
    assert await periodic_service.running_task(session, TASK) is None


# --- планировщик: расписание переживает пересборку ---------------------------


def _scheduler_with(rows, app=None):
    """Планировщик поверх подставленной таблицы: БД для этого не нужна."""
    from app.tasks.beat import DatabaseScheduler
    from app.tasks.celery_app import celery_app

    class Fake(DatabaseScheduler):
        def _rows(self):
            return rows

    return Fake(app=app or celery_app, lazy=True)


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


# --- планировщик: рестарт beat не шлёт рассылки вне расписания ----------------

PARK_TZ = ZoneInfo("Asia/Bishkek")


def _park_app(now_utc):
    """Свой celery с поясом парка: тест не зависит от TZ окружения."""
    from celery import Celery

    app = Celery("beat-test")
    app.conf.update(timezone="Asia/Bishkek", enable_utc=True)
    app.now = lambda: now_utc.astimezone(PARK_TZ)
    return app


def _cron_row(crontab, last_run_at):
    return PeriodicTask(
        id=1, name="задача", task=TASK, crontab=crontab,
        interval_seconds=None, args=None, enabled=True, last_run_at=last_run_at,
    )


# Прод 29.10: beat перезапущен в 18:57 UTC (00:57 30.10 по Бишкеку); до этого
# каждая задача отработала в срок, в БД время запуска в UTC.
RESTART = datetime(2026, 10, 29, 18, 57, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "crontab,last_run_utc,next_local",
    [
        ("0 9 * * *", (29, 3, 0), "30.10 09:00"),  # утреннее напоминание
        ("0 10 * * *", (29, 4, 0), "30.10 10:00"),  # фотоконтроль
        ("45 21 * * *", (29, 15, 45), "30.10 21:45"),  # предупреждение до срока
        ("0 22 * * *", (29, 16, 0), "30.10 22:00"),  # «блокировка включена»
        ("10 22 * * *", (29, 16, 10), "30.10 22:10"),  # админ-дайджест
    ],
)
def test_restart_keeps_park_time(crontab, last_run_utc, next_local):
    """Утренние уходили сразу при старте, вечерние — ночью (по часам UTC)."""
    day, hour, minute = last_run_utc
    row = _cron_row(crontab, datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc))

    entry = _scheduler_with([row], app=_park_app(RESTART))._build()["задача"]
    state = entry.is_due()

    assert state.is_due is False
    next_run = (RESTART + timedelta(seconds=state.next)).astimezone(PARK_TZ)
    assert f"{next_run:%d.%m %H:%M}" == next_local


def test_morning_task_due_at_park_morning():
    """Обратная сторона: в 09:00 по Бишкеку утренняя задача всё-таки уходит."""
    row = _cron_row("0 9 * * *", datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc))
    now = datetime(2026, 10, 30, 3, 0, 5, tzinfo=timezone.utc)

    entry = _scheduler_with([row], app=_park_app(now))._build()["задача"]

    assert entry.is_due().is_due is True
