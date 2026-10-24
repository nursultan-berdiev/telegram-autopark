"""Аудит действий администратора: константы, запись, ASGI-middleware.

Выделено из main.py в один модуль. Middleware — ЧИСТЫЙ ASGI (не
BaseHTTPMiddleware), чтобы не буферизовать ответ и не ломать стриминговый /mcp.

Охват (осознанно): пишутся УСПЕШНЫЕ (<400) мутации, помеченные зависимостями
авторизации через `mark()` — это `require_admin_actor` (бот и веб), `current_admin`
(веб-формы Jinja-админки) и `require_import_actor` (импорт штрафов). Отказы
(401/403, в т.ч. CSRF) в таблицу не пишутся — для них остаётся лог
(`auth._require_csrf_header`, `auth._admin_or_403` вызываются до `mark`).
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db.base import async_session_maker
from app.db.models import AuditLog

log = logging.getLogger(__name__)

SOURCE_BOT = "bot"
SOURCE_WEB = "web"
_SCOPE_ACTOR = "audit_actor"
_SCOPE_SOURCE = "audit_source"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def mark(scope: Scope, actor: int, source: str) -> None:
    """Пометить запрос для аудита — зовут зависимости авторизации."""
    scope[_SCOPE_ACTOR] = actor
    scope[_SCOPE_SOURCE] = source


async def _record(scope: Scope, status_code: int) -> None:
    route = scope.get("route")
    # Шаблон маршрута (/cars/{car_id}/...) — без конкретных id; сами id в params.
    path = getattr(route, "path", None) or scope.get("path", "")
    params = dict(scope.get("path_params") or {}) or None
    try:
        async with async_session_maker() as session:
            session.add(
                AuditLog(
                    actor_tg_user_id=scope[_SCOPE_ACTOR],
                    source=scope.get(_SCOPE_SOURCE, SOURCE_WEB),
                    method=scope["method"],
                    path=path[:128],
                    status_code=status_code,
                    params=params,
                )
            )
            await session.commit()
    except SQLAlchemyError:
        log.exception("аудит: не записал действие (ошибка БД)")
    except Exception:  # noqa: BLE001 — аудит никогда не ломает основной запрос
        log.exception("аудит: не записал действие")


class AuditMiddleware:
    """Пишет успешные админ-мутации в audit_log (см. охват в докстринге модуля)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = {"code": 500}

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, _send)

        if (
            scope.get(_SCOPE_ACTOR) is not None
            and scope.get("method") in _MUTATING_METHODS
            and status["code"] < 400
        ):
            await _record(scope, status["code"])
