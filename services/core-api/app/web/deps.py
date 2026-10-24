"""Зависимости админки: кто вошёл и пускать ли его."""
from __future__ import annotations

from fastapi import Cookie, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app import audit
from app.config import settings
from app.web.session import admin_from_cookie


class NotLoggedIn(HTTPException):
    """Отдельный тип: на страницах это редирект, а не голый 401."""

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail="нужен вход")


async def current_admin(
    request: Request, autopark_admin: str | None = Cookie(default=None)
) -> int:
    """Админ веб-страниц по куке. Права проверяются на каждом запросе (а не
    только при входе): админа могли убрать из ADMIN_IDS после выдачи куки, подпись
    при этом осталась бы валидной. Выключенная админка — явный 404 (vs «не вошёл»)."""
    if not settings.admin_web_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="админка не настроена"
        )
    tg = admin_from_cookie(autopark_admin)
    if tg is None:
        raise NotLoggedIn()
    audit.mark(request.scope, tg, audit.SOURCE_WEB)
    return tg


def login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/login-required", status_code=303)
