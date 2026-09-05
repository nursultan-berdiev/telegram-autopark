"""Аутентификация сервис-к-сервису и роль пользователя.

Токены разделены по областям: INGEST_TOKEN пускает только в ингест
телеметрии, команды — только по CORE_API_TOKEN (plan/03).
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.config import settings


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="нужен Bearer-токен"
        )
    return authorization.split(" ", 1)[1].strip()


async def require_core(authorization: str | None = Header(default=None)) -> str:
    token = _bearer(authorization)
    if settings.core_api_token and token == settings.core_api_token:
        return "core"
    if settings.ingest_token and token == settings.ingest_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="INGEST_TOKEN не даёт прав на доменные вызовы",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="неверный токен")


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


async def require_admin_actor(
    _: str = Depends(require_core), actor: int | None = Depends(tg_user_id)
) -> int:
    """Чувствительные действия проверяем на сервере, а не по слову клиента."""
    return _admin_or_403(actor)


async def require_import_actor(
    _: str = Depends(require_fines_import), actor: int | None = Depends(tg_user_id)
) -> int:
    """Узкий токен всё равно не отменяет вопроса «от чьего имени заведён штраф»."""
    return _admin_or_403(actor)
