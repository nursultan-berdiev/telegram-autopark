"""Зависимости админки: кто вошёл и пускать ли его."""
from __future__ import annotations

from fastapi import Cookie, HTTPException, status
from fastapi.responses import RedirectResponse

from app.config import settings
from app.web.session import read


class NotLoggedIn(HTTPException):
    """Отдельный тип: на страницах это редирект, а не голый 401."""

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail="нужен вход")


async def current_admin(autopark_admin: str | None = Cookie(default=None)) -> int:
    """Права проверяются на каждом запросе, а не только при входе.

    Админа могли убрать из ADMIN_IDS уже после выдачи куки — подпись при
    этом осталась бы валидной.
    """
    if not settings.admin_web_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="админка не настроена"
        )
    tg_user_id = read(autopark_admin)
    if tg_user_id is None or not settings.is_admin(tg_user_id):
        raise NotLoggedIn()
    return tg_user_id


def login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/login-required", status_code=303)
