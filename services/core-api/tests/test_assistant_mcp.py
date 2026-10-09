"""Аутентификация /mcp (ASGI-обёртка) и интеграция MCP-сервера (initialize/tools)."""
import httpx
import pytest

from app.assistant.auth import MCPAuth


class _Inner:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send) -> None:
        self.called = True


async def _run_auth(mw, headers) -> list:
    sent: list = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b""}

    await mw({"type": "http", "headers": headers}, receive, send)
    return sent


async def test_mcp_auth_right_token_passes():
    inner = _Inner()
    sent = await _run_auth(MCPAuth(inner, "secret"), [(b"authorization", b"Bearer secret")])
    assert inner.called and sent == []


@pytest.mark.parametrize(
    "headers",
    [
        [],  # нет заголовка
        [(b"authorization", b"Bearer nope")],  # неверный токен
        [(b"authorization", b"secret")],  # без Bearer
    ],
)
async def test_mcp_auth_rejects(headers):
    inner = _Inner()
    sent = await _run_auth(MCPAuth(inner, "secret"), headers)
    assert not inner.called and sent[0]["status"] == 401


async def test_mcp_auth_empty_token_fail_closed():
    """Пустой токен → доступа нет (fail-closed), даже с валидным на вид заголовком."""
    inner = _Inner()
    sent = await _run_auth(MCPAuth(inner, ""), [(b"authorization", b"Bearer ")])
    assert not inner.called and sent[0]["status"] == 401


async def test_mcp_server_initialize_and_tools_list():
    """Смонтированный MCP отдаёт initialize и список инструментов; без токена — 401.

    Host=localhost входит в allowed_hosts (DNS-rebinding защита FastMCP), иначе был
    бы 403 — именно это ломало бы интеграцию с gateway."""
    from app.assistant.mcp_server import mcp, mcp_app

    guarded = MCPAuth(mcp_app, "secret")
    ok = {
        "Authorization": "Bearer secret",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "1"}}}

    async with mcp.session_manager.run():
        transport = httpx.ASGITransport(app=guarded)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            r = await c.post("/", headers=ok, json=init)
            assert r.status_code == 200 and "fleet" in r.text
            tl = await c.post("/", headers=ok, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            assert "fleet_overview" in tl.text and "blocked_cars" in tl.text
            # Без токена — отказ ещё до MCP.
            bad = await c.post("/", headers={k: v for k, v in ok.items() if k != "Authorization"}, json=init)
            assert bad.status_code == 401
