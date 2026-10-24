"""Phase 1 веб-админки: двойная авторизация (кука/бот), CSRF, аудит, журналы."""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import AuditLog
from app.web.session import COOKIE_NAME, issue

ADMIN = 111       # есть в ADMIN_IDS (conftest)
NOT_ADMIN = 999   # нет
CSRF = {"X-Requested-With": "fleet-admin"}


def _web(tg: int = ADMIN, *, csrf: bool = False, raw: str | None = None) -> dict:
    """Заголовки веб-клиента: куку шлём заголовком Cookie, Bearer пустой."""
    cookie = raw if raw is not None else issue(tg)
    headers = {"Authorization": "", "Cookie": f"{COOKIE_NAME}={cookie}"}
    if csrf:
        headers.update(CSRF)
    return headers


# --- Двойная авторизация: куки-путь -----------------------------------------
async def test_cookie_admin_reads_admin_endpoint(client):
    r = await client.get("/periodic-tasks", headers=_web())
    assert r.status_code == 200


async def test_cookie_mutation_requires_csrf(client):
    r = await client.post("/cars", json={"plate": "01KG777AAA"}, headers=_web())
    assert r.status_code == 403
    ok = await client.post("/cars", json={"plate": "01KG777AAA"}, headers=_web(csrf=True))
    assert ok.status_code == 201


async def test_cookie_delete_requires_csrf(client, admin_headers):
    created = await client.post("/cars", json={"plate": "01KG222AAA"}, headers=admin_headers)
    car_id = created.json()["id"]
    no = await client.delete(f"/cars/{car_id}", headers=_web())
    assert no.status_code == 403
    ok = await client.delete(f"/cars/{car_id}", headers=_web(csrf=True))
    assert ok.status_code == 204


async def test_non_admin_cookie_rejected(client):
    r = await client.get("/periodic-tasks", headers=_web(NOT_ADMIN))
    assert r.status_code in (401, 403)


# --- Чтение домена по админ-куке (viewer) -----------------------------------
async def test_cookie_viewer_reads_domain_get(client):
    cars = await client.get("/cars", headers=_web())
    assert cars.status_code == 200
    rep = await client.get("/reports/cars-drivers", headers=_web())
    assert rep.status_code == 200


async def test_cookie_cannot_do_require_core_mutation(client):
    # require_core POST (сервисный, не админский) по куке без токена → 401:
    # кука пускает только на безопасные методы, запись требует токен.
    r = await client.post("/outbox/1/sent", headers=_web(csrf=True))
    assert r.status_code == 401


async def test_non_admin_cookie_cannot_read_domain(client):
    r = await client.get("/cars", headers=_web(NOT_ADMIN))
    assert r.status_code in (401, 403)


# --- Куки-ветка не перехватывает путь бота ----------------------------------
async def test_non_admin_cookie_falls_back_to_bot(client, admin_headers):
    headers = {**admin_headers, "Cookie": f"{COOKIE_NAME}={issue(NOT_ADMIN)}"}
    r = await client.post("/cars", json={"plate": "01KG333AAA"}, headers=headers)
    assert r.status_code == 201


async def test_broken_cookie_falls_back_to_bot(client, admin_headers):
    headers = {**admin_headers, "Cookie": f"{COOKIE_NAME}=not-a-valid-signature"}
    r = await client.post("/cars", json={"plate": "01KG444AAA"}, headers=headers)
    assert r.status_code == 201


async def test_bot_path_still_works(client, admin_headers):
    r = await client.post("/cars", json={"plate": "01KG123AAA"}, headers=admin_headers)
    assert r.status_code == 201


# --- login-link остаётся только для бота ------------------------------------
async def test_login_link_cookie_forbidden(client):
    r = await client.post("/admin/login-link", headers=_web(csrf=True))
    assert r.status_code in (401, 403)


async def test_login_link_bot_ok(client, admin_headers):
    r = await client.post("/admin/login-link", headers=admin_headers)
    assert r.status_code == 200 and "url" in r.json()


# --- SPA-сессия: whoami / logout --------------------------------------------
async def test_whoami(client):
    ok = await client.get("/whoami", headers=_web())
    assert ok.status_code == 200
    assert ok.json() == {"tg_user_id": ADMIN, "is_admin": True}
    anon = await client.get("/whoami", headers={"Authorization": ""})
    assert anon.status_code == 401


async def test_web_logout(client):
    anon = await client.post("/web-logout", headers={"Authorization": "", **CSRF})
    assert anon.status_code == 401
    no_csrf = await client.post("/web-logout", headers=_web())
    assert no_csrf.status_code == 403
    ok = await client.post("/web-logout", headers=_web(csrf=True))
    assert ok.status_code == 204
    assert COOKIE_NAME in ok.headers.get("set-cookie", "")


# --- Аудит -------------------------------------------------------------------
async def test_audit_recorded_on_mutation(client, admin_headers, session):
    resp = await client.post("/cars", json={"plate": "01KG555AAA"}, headers=admin_headers)
    assert resp.status_code == 201
    rows = (await session.scalars(select(AuditLog))).all()
    assert any(
        r.method == "POST" and r.path.endswith("/cars") and r.source == "bot"
        and r.actor_tg_user_id == ADMIN and r.status_code == 201
        for r in rows
    )


async def test_audit_marks_web_source(client, session):
    await client.post("/cars", json={"plate": "01KG900AAA"}, headers=_web(csrf=True))
    rows = (await session.scalars(select(AuditLog).where(AuditLog.source == "web"))).all()
    assert any(r.path.endswith("/cars") and r.actor_tg_user_id == ADMIN for r in rows)


async def test_audit_skips_failed_mutations(client, admin_headers, session):
    await client.post("/cars", json={"plate": "01KG000AAA"}, headers=admin_headers)  # 201
    dup = await client.post("/cars", json={"plate": "01KG000AAA"}, headers=admin_headers)  # 409
    assert dup.status_code == 409
    rows = (await session.scalars(select(AuditLog).where(AuditLog.status_code >= 400))).all()
    assert rows == [], "отказы (>=400) в журнал аудита не пишем"


async def test_audit_failure_does_not_break_request(client, admin_headers, monkeypatch):
    def _boom():
        raise RuntimeError("БД аудита недоступна")

    monkeypatch.setattr("app.audit.async_session_maker", _boom)
    r = await client.post("/cars", json={"plate": "01KG661AAA"}, headers=admin_headers)
    assert r.status_code == 201  # сбой записи аудита не ломает основной запрос


async def test_audit_read_filters(client, admin_headers):
    await client.post("/cars", json={"plate": "01KG111AAA"}, headers=admin_headers)
    bot = await client.get("/audit", headers=admin_headers, params={"source": "bot"})
    assert bot.status_code == 200 and all(row["source"] == "bot" for row in bot.json())
    web = await client.get("/audit", headers=admin_headers, params={"source": "web"})
    assert web.json() == []
    actor = await client.get("/audit", headers=admin_headers, params={"actor": ADMIN})
    assert all(row["actor_tg_user_id"] == ADMIN for row in actor.json())


async def test_audit_requires_admin(client):
    r = await client.get("/audit", headers={"Authorization": ""})
    assert r.status_code in (401, 403)


# --- Журнал очереди отправок -------------------------------------------------
async def test_outbound_messages_admin_list(client, admin_headers, session):
    from app.domain import outbox as outbox_domain

    await outbox_domain.enqueue(session, recipient_tg_user_id=123, text="привет", kind="digest")
    await outbox_domain.enqueue(session, recipient_tg_user_id=456, text="ещё", kind="reminder")
    await session.commit()

    r = await client.get("/outbound-messages", headers=admin_headers)
    assert r.status_code == 200
    assert any(m["recipient_tg_user_id"] == 123 and m["kind"] == "digest" for m in r.json())

    only = await client.get("/outbound-messages", headers=admin_headers, params={"kind": "reminder"})
    assert only.status_code == 200 and all(m["kind"] == "reminder" for m in only.json())


async def test_outbound_messages_requires_admin(client):
    r = await client.get("/outbound-messages", headers={"Authorization": ""})
    assert r.status_code in (401, 403)
