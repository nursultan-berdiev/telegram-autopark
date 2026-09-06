"""Веб-админка: вход по одноразовой ссылке и права на страницах."""
from __future__ import annotations

from datetime import timedelta

import pytest
from app.config import settings
from app.domain import admin_auth
from app.domain import periodic as periodic_service
from app.web import session as web_session
from tests.conftest import ADMIN_ID

TASK = "app.tasks.ping.ping"
NOT_ADMIN = 999_999


async def _link(client, session):
    token, _ = await admin_auth.issue_token(session, ADMIN_ID)
    return token


# --- выдача ссылки ----------------------------------------------------------


async def test_login_link_requires_admin(client):
    assert (await client.post("/admin/login-link")).status_code in (401, 403)


async def test_login_link_contains_token(admin_client):
    resp = await admin_client.post("/admin/login-link")

    assert resp.status_code == 200
    assert "/admin/login?token=" in resp.json()["url"]


async def test_token_stored_hashed(session):
    """Утечка таблицы не должна давать вход."""
    from sqlalchemy import select

    from app.db.models import AdminLoginToken

    token, _ = await admin_auth.issue_token(session, ADMIN_ID)

    stored = await session.scalar(select(AdminLoginToken.token_hash))
    assert stored != token
    assert stored == admin_auth.hash_token(token)


async def test_new_link_invalidates_previous(session):
    """Забытая в чате старая ссылка не должна работать неделю спустя."""
    old_token, _ = await admin_auth.issue_token(session, ADMIN_ID)
    await admin_auth.issue_token(session, ADMIN_ID)

    assert await admin_auth.redeem_token(session, old_token) is None


# --- обмен ссылки на сессию -------------------------------------------------


async def test_token_is_single_use(session):
    token, _ = await admin_auth.issue_token(session, ADMIN_ID)

    assert await admin_auth.redeem_token(session, token) == ADMIN_ID
    assert await admin_auth.redeem_token(session, token) is None


async def test_expired_token_rejected(session):
    from app.db.models import AdminLoginToken
    from sqlalchemy import select

    token, _ = await admin_auth.issue_token(session, ADMIN_ID)
    row = await session.scalar(select(AdminLoginToken))
    row.expires_at = row.expires_at - timedelta(hours=1)
    await session.commit()

    assert await admin_auth.redeem_token(session, token) is None


async def test_unknown_token_rejected(session):
    assert await admin_auth.redeem_token(session, "выдуманный") is None


async def test_login_sets_cookie_and_redirects(client, session):
    token = await _link(client, session)

    resp = await client.get(f"/admin/login?token={token}", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"
    assert web_session.COOKIE_NAME in resp.cookies


async def test_login_with_bad_token_is_403(client):
    resp = await client.get("/admin/login?token=мусор", follow_redirects=False)

    assert resp.status_code == 403


# --- права на страницах -----------------------------------------------------


async def test_pages_redirect_anonymous(client):
    resp = await client.get("/admin", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login-required"


async def test_cookie_of_removed_admin_stops_working(client):
    """Подпись остаётся валидной и после удаления из ADMIN_IDS — права
    проверяются на каждом запросе, а не только при входе."""
    client.cookies.set(web_session.COOKIE_NAME, web_session.issue(NOT_ADMIN))

    resp = await client.get("/admin", follow_redirects=False)

    assert resp.status_code == 303


async def test_forged_cookie_rejected(client):
    client.cookies.set(web_session.COOKIE_NAME, "forged.signature.value")

    assert (await client.get("/admin", follow_redirects=False)).status_code == 303


@pytest.mark.parametrize(
    "path",
    [
        "/admin/schedules",
        "/admin/schedules/1/toggle",
        "/admin/schedules/1/period",
        "/admin/schedules/1/delete",
        "/admin/schedules/1/run",
    ],
)
async def test_write_actions_require_login(client, path):
    resp = await client.post(path, data={}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login-required"


# --- страницы под сессией ---------------------------------------------------


@pytest.fixture
def logged_in(client):
    client.cookies.set(web_session.COOKIE_NAME, web_session.issue(ADMIN_ID))
    return client


async def test_index_lists_schedules(logged_in, session):
    await periodic_service.create_task(
        session, name="проверка штрафов", task=TASK, interval_seconds=86400
    )

    resp = await logged_in.get("/admin")

    assert resp.status_code == 200
    assert "проверка штрафов" in resp.text


async def test_create_schedule_through_form(logged_in, session):
    resp = await logged_in.post(
        "/admin/schedules",
        data={"name": "ночная", "task": TASK, "interval_seconds": "3600", "crontab": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    tasks = await periodic_service.list_tasks(session)
    assert [t.name for t in tasks] == ["ночная"]


async def test_form_rejects_unknown_task(logged_in, session):
    """Свободный ввод имени означал бы запуск произвольной точки входа."""
    resp = await logged_in.post(
        "/admin/schedules",
        data={"name": "чужая", "task": "os.system", "interval_seconds": "3600", "crontab": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert await periodic_service.list_tasks(session) == []


async def test_bad_period_returns_to_page_with_error(logged_in, session):
    resp = await logged_in.post(
        "/admin/schedules",
        data={"name": "битая", "task": TASK, "interval_seconds": "3600", "crontab": "0 9 * * *"},
        follow_redirects=False,
    )

    assert "error=" in resp.headers["location"]
    assert await periodic_service.list_tasks(session) == []


async def test_toggle_switches_enabled(logged_in, session):
    row = await periodic_service.create_task(
        session, name="штрафы", task=TASK, interval_seconds=3600
    )

    await logged_in.post(f"/admin/schedules/{row.id}/toggle", follow_redirects=False)

    await session.refresh(row)
    assert row.enabled is False


async def test_admin_hidden_when_not_configured(client, monkeypatch):
    """Админку с предсказуемой подписью лучше не поднимать вовсе."""
    monkeypatch.setattr(settings, "admin_session_secret", "")

    assert (await client.get("/admin", follow_redirects=False)).status_code == 404


async def test_non_numeric_interval_is_page_error_not_500(logged_in, session):
    """type=number защищает только браузер: curl и прокси шлют что угодно."""
    resp = await logged_in.post(
        "/admin/schedules",
        data={"name": "кривая", "task": TASK, "interval_seconds": "abc", "crontab": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert await periodic_service.list_tasks(session) == []


async def test_non_numeric_interval_on_edit_is_handled(logged_in, session):
    row = await periodic_service.create_task(
        session, name="штрафы", task=TASK, interval_seconds=3600
    )

    resp = await logged_in.post(
        f"/admin/schedules/{row.id}/period",
        data={"interval_seconds": "почаще", "crontab": ""},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    await session.refresh(row)
    assert row.interval_seconds == 3600, "период не должен обнулиться от мусора"


async def test_success_is_not_shown_as_error(logged_in, session, monkeypatch):
    """Успех и ошибка — разные баннеры: жёлтый на удачу пугает зря."""
    from app.tasks.celery_app import celery_app

    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: sent.append((a, k)))
    row = await periodic_service.create_task(
        session, name="штрафы", task=TASK, interval_seconds=3600
    )

    resp = await logged_in.post(
        f"/admin/schedules/{row.id}/run", follow_redirects=False
    )

    assert sent, "задача должна уйти в очередь"
    assert "msg=" in resp.headers["location"]
    assert "error=" not in resp.headers["location"]


async def test_broken_broker_does_not_hang_the_page(logged_in, session, monkeypatch):
    """Недоступный брокер — понятный баннер, а не зависшая страница."""
    from app.tasks.celery_app import celery_app

    def boom(*args, **kwargs):
        raise ConnectionError("брокер недоступен")

    monkeypatch.setattr(celery_app, "send_task", boom)
    row = await periodic_service.create_task(
        session, name="штрафы", task=TASK, interval_seconds=3600
    )

    resp = await logged_in.post(
        f"/admin/schedules/{row.id}/run", follow_redirects=False
    )

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


async def test_expired_login_tokens_are_purged(session):
    """Использованные и просроченные ссылки иначе копятся в таблице вечно."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.db.models import AdminLoginToken

    await admin_auth.issue_token(session, ADMIN_ID)
    row = await session.scalar(select(AdminLoginToken))
    row.expires_at = row.expires_at - timedelta(days=2)
    await session.commit()
    fresh, _ = await admin_auth.issue_token(session, ADMIN_ID + 1)

    removed = await admin_auth.purge_expired(session)

    assert removed == 1
    assert await admin_auth.redeem_token(session, fresh) == ADMIN_ID + 1, "свежая жива"


def test_cleanup_job_is_registered():
    """Функция без вызова — мёртвый код; проверяем, что джоба есть."""
    import inspect

    from app import jobs

    source = inspect.getsource(jobs.start_jobs)
    assert "cleanup_login_tokens" in source
