"""ASGI-обёртка: bearer-токен на MCP-эндпоинт.

Чистый ASGI (а не `BaseHTTPMiddleware`): тот ненадёжен для streaming/SSE, которым
пользуется streamable-HTTP MCP. Fail-closed: при пустом токене — отказ (монтируем
MCP только когда токен задан, но проверка на всякий случай тоже закрыта).
"""
from __future__ import annotations

import secrets

from starlette.types import ASGIApp, Receive, Scope, Send

_UNAUTHORIZED = b'{"detail":"mcp: unauthorized"}'


class MCPAuth:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._authorized(scope):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": _UNAUTHORIZED})
            return
        await self._app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        if not self._token:  # fail-closed: без токена доступа нет
            return False
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")
        return secrets.compare_digest(provided, f"Bearer {self._token}")
