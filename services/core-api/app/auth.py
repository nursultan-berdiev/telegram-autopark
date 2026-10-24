"""Аутентификация сервис-к-сервису, веб-сессия и роль пользователя.

Токены разделены по областям: INGEST_TOKEN пускает только в ингест телеметрии,
команды — только по CORE_API_TOKEN (plan/03). Админ-действия доступны ЛИБО боту
(CORE_API_TOKEN + X-TG-User-Id), ЛИБО браузеру (подписанная кука веб-админки).
"""
from __future__ import annotations

import logging

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from app import audit
from app.config import settings
from app.web.session import admin_from_cookie

log = logging.getLogger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="нужен Bearer-токен"
        )
    return authorization.split(" ", 1)[1].strip()


async def _assert_core_token(authorization: str | None) -> None:
    """Строгая проверка сервисного токена (без веб-куки). 401/403 как и прежде."""
    token = _bearer(authorization)
    if settings.core_api_token and token == settings.core_api_token:
        return
    if settings.ingest_token and token == settings.ingest_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INGEST_TOKEN не даёт прав на доменные вызовы",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="неверный токен")


async def require_core(
    request: Request,
    authorization: str | None = Header(default=None),
    autopark_admin: str | None = Cookie(default=None),
) -> str:
    """Доменный доступ: сервисный токен (бот) ЛИБО — ТОЛЬКО на безопасных методах
    (GET/HEAD/OPTIONS) — админ-кука веб-админки, чтобы SPA читал теми же GET-ами.

    На мутациях под require_core (outbox/alerts/регистрация/платежи) кука
    игнорируется и нужен токен: ambient-кука не должна инициировать запись (CSRF).
    Админ-мутации идут через require_admin_actor (там своя CSRF-защита).
    """
    if request.method in _SAFE_METHODS and admin_from_cookie(autopark_admin) is not None:
        return "web-admin"
    await _assert_core_token(authorization)
    return "core"


async def require_ingest(authorization: str | None = Header(default=None)) -> str:
    """Ингест принимает ТОЛЬКО INGEST_TOKEN — решение S6 плана."""
    token = _bearer(authorization)
    if settings.ingest_token and token == settings.ingest_token:
        return "ingest"
    if settings.core_api_token and token == settings.core_api_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ингест принимает только INGEST_TOKEN",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="неверный токен")


async def require_fines_import(authorization: str | None = Header(default=None)) -> str:
    """Импорт штрафов принимает ТОЛЬКО FINES_IMPORT_TOKEN.

    Токен уезжает в расширение на машине владельца, поэтому область у него
    ровно одна: мастер-ключ там дал бы и разблокировку двигателя.
    """
    token = _bearer(authorization)
    if settings.fines_import_token and token == settings.fines_import_token:
        return "fines-import"
    if settings.core_api_token and token == settings.core_api_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="импорт штрафов принимает только FINES_IMPORT_TOKEN",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="неверный токен")


async def tg_user_id(x_tg_user_id: int | None = Header(default=None)) -> int | None:
    return x_tg_user_id


def _admin_or_403(actor: int | None) -> int:
    if not settings.is_admin(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="действие доступно только админу"
        )
    return int(actor)  # type: ignore[arg-type]


def _require_csrf_header(request: Request) -> None:
    """Куки-авторизация неявная → мутации требуют заголовок X-Requested-With.

    Кросс-сайтом его не выставить без CORS-префлайта (CORS мы не включаем); в
    паре с SameSite-кукой это дешёвая защита от CSRF. Путь бота (Bearer-токен) в
    этой защите не нуждается. Отказ логируем — в таблицу аудита отказы не пишем.
    """
    if request.method not in _SAFE_METHODS and not request.headers.get("x-requested-with"):
        log.warning(
            "отклонён запрос без X-Requested-With (возможный CSRF): %s %s",
            request.method,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="нужен заголовок X-Requested-With",
        )


def require_csrf(request: Request) -> None:
    """Зависимость: требовать X-Requested-With на куки-мутации вне
    `require_admin_actor` (например POST /web-logout)."""
    _require_csrf_header(request)


async def require_admin_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    actor: int | None = Depends(tg_user_id),
    autopark_admin: str | None = Cookie(default=None),
) -> int:
    """Админ-действие: пускаем ЛИБО по веб-сессии (подписанная кука), ЛИБО как
    бот (CORE_API_TOKEN + X-TG-User-Id). Одни и те же роутеры служат и боту, и
    веб-админке; права проверяются на сервере в обоих случаях."""
    web_tg = admin_from_cookie(autopark_admin)
    if web_tg is not None:
        _require_csrf_header(request)
        audit.mark(request.scope, web_tg, audit.SOURCE_WEB)
        return web_tg
    # Путь бота/сервиса — строго CORE_API_TOKEN + актёр-админ.
    await _assert_core_token(authorization)
    admin = _admin_or_403(actor)
    audit.mark(request.scope, admin, audit.SOURCE_BOT)
    return admin


async def require_admin_bot(
    request: Request,
    _: str = Depends(require_core),
    actor: int | None = Depends(tg_user_id),
) -> int:
    """Только путь бота (CORE_API_TOKEN + X-TG-User-Id). Для действий, которые
    веб-сессия выполнять НЕ должна — напр. выпуск себе же новой ссылки/куки через
    /admin/login-link: иначе украденная 24-часовая кука стала бы вечной, а
    отозвать её нельзя (сессия stateless)."""
    admin = _admin_or_403(actor)
    audit.mark(request.scope, admin, audit.SOURCE_BOT)
    return admin


async def require_import_actor(
    request: Request,
    _: str = Depends(require_fines_import),
    actor: int | None = Depends(tg_user_id),
) -> int:
    """Узкий токен всё равно не отменяет вопроса «от чьего имени заведён штраф»."""
    admin = _admin_or_403(actor)
    audit.mark(request.scope, admin, audit.SOURCE_BOT)
    return admin
