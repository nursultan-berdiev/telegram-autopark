"""SSO в Traccar: /traccar/sso отдаёт URL карты с короткоживущим токеном."""
from __future__ import annotations

import functools
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.clients import adapter
from app.clients.adapter import AdapterError
from app.config import settings
from app.db.models import AuditLog
from app.web.session import COOKIE_NAME, issue

ADMIN = 111
PUBLIC = "https://tracker.example.kg"


@pytest.fixture
def public_url(monkeypatch):
    monkeypatch.setattr(settings, "traccar_public_url", PUBLIC)


@pytest.fixture
def mint(monkeypatch):
    mock = AsyncMock(return_value="TOK/EN+123")
    monkeypatch.setattr("app.clients.adapter.create_sso_token", mock)
    return mock


# --- эндпойнт /traccar/sso --------------------------------------------------


async def test_returns_url_with_quoted_token_and_no_store(admin_client, public_url, mint):
    resp = await admin_client.post("/traccar/sso")
    assert resp.status_code == 200
    # Токен URL-экранирован (/, + превратились бы в разделители).
    assert resp.json()["url"] == f"{PUBLIC}/?token=TOK%2FEN%2B123"
    assert resp.headers["cache-control"] == "no-store"  # токен в query — не кэшируем
    mint.assert_awaited_once()


async def test_sso_access_is_audited(admin_client, session, public_url, mint):
    """В журнале остаётся след, кто открыл карту (ради этого ручка — POST)."""
    await admin_client.post("/traccar/sso")
    rows = (await session.scalars(select(AuditLog).where(AuditLog.path == "/traccar/sso"))).all()
    assert len(rows) == 1
    assert rows[0].method == "POST"
    assert rows[0].actor_tg_user_id == ADMIN


async def test_cookie_admin_allowed(client, public_url, mint):
    # POST куки требует CSRF-заголовок (его и шлёт SPA).
    headers = {"Authorization": "", "Cookie": f"{COOKIE_NAME}={issue(ADMIN)}", "X-Requested-With": "fleet-admin"}
    resp = await client.post("/traccar/sso", headers=headers)
    assert resp.status_code == 200


async def test_service_token_without_actor_rejected(client, public_url, mint):
    """Один CORE_API_TOKEN без X-TG-User-Id карту не откроет — нужен админ-актор (аудит)."""
    resp = await client.post("/traccar/sso")  # client шлёт только Bearer, без X-TG-User-Id
    assert resp.status_code == 403
    mint.assert_not_awaited()


async def test_no_auth_rejected(client, public_url, mint):
    resp = await client.post("/traccar/sso", headers={"Authorization": ""})
    assert resp.status_code == 401
    mint.assert_not_awaited()


async def test_not_configured_503(admin_client, mint, monkeypatch):
    monkeypatch.setattr(settings, "traccar_public_url", "")
    resp = await admin_client.post("/traccar/sso")
    assert resp.status_code == 503
    mint.assert_not_awaited()


async def test_adapter_error_502(admin_client, public_url, monkeypatch):
    monkeypatch.setattr(
        "app.clients.adapter.create_sso_token", AsyncMock(side_effect=AdapterError("адаптер лёг"))
    )
    resp = await admin_client.post("/traccar/sso")
    assert resp.status_code == 502


# --- клиент adapter.create_sso_token (httpx.MockTransport) ------------------


def _client_with(monkeypatch, handler):
    monkeypatch.setattr(settings, "adapter_url", "http://tracker-adapter:8001")
    monkeypatch.setattr(settings, "adapter_token", "adtok")
    monkeypatch.setattr(
        "app.clients.adapter.httpx.AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )


async def test_create_sso_token_sends_bearer_and_returns_token(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"token": "ABC"})

    _client_with(monkeypatch, handler)
    assert await adapter.create_sso_token() == "ABC"
    assert seen["url"] == "http://tracker-adapter:8001/sso-token"
    assert seen["auth"] == "Bearer adtok"


async def test_create_sso_token_4xx_raises(monkeypatch):
    _client_with(monkeypatch, lambda r: httpx.Response(502))
    with pytest.raises(AdapterError):
        await adapter.create_sso_token()


async def test_create_sso_token_bad_body_raises(monkeypatch):
    _client_with(monkeypatch, lambda r: httpx.Response(200, text="not json"))
    with pytest.raises(AdapterError):
        await adapter.create_sso_token()


async def test_create_sso_token_empty_raises(monkeypatch):
    _client_with(monkeypatch, lambda r: httpx.Response(200, json={"token": ""}))
    with pytest.raises(AdapterError):
        await adapter.create_sso_token()
