"""SPA-сессия веб-админки: кто вошёл и выход (куки-авторизация).

Вход по-прежнему через бота (одноразовая ссылка → кука). Здесь — проверка
сессии и выход для SPA. Пути НЕ под /admin, поэтому при отсутствии входа
обработчик NotLoggedIn отдаёт JSON 401 (а не редирект на HTML-страницу).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.auth import require_csrf
from app.config import settings
from app.web.deps import current_admin
from app.web.session import COOKIE_NAME

router = APIRouter()


class WhoAmIDTO(BaseModel):
    tg_user_id: int
    is_admin: bool


@router.get("/whoami", response_model=WhoAmIDTO)
async def whoami(tg: int = Depends(current_admin)) -> WhoAmIDTO:
    """Проверка веб-сессии для SPA: кто вошёл. Без входа — JSON 401."""
    return WhoAmIDTO(tg_user_id=tg, is_admin=True)


@router.post("/web-logout", status_code=204, response_model=None)
async def web_logout(
    _: int = Depends(current_admin),
    __: None = Depends(require_csrf),
) -> Response:
    """Выход из веб-сессии: удаляет куку.

    Подпись stateless — сервер токен НЕ отзывает: украденная кука живёт до
    истечения SESSION_TTL (24 ч). Это осознанный компромисс (см. docstring
    `app/web/session.py`). Куку удаляем с теми же атрибутами (path/samesite/
    secure), что при выдаче в `/admin/login`, иначе браузер может её не удалить.
    """
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.admin_base_url.startswith("https://"),
    )
    return response
