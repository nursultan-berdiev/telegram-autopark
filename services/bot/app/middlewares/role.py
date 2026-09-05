"""Middleware роли: спрашивает роль у core-api, в БД больше не ходит.

Роль кэшируется на короткий TTL — иначе на каждый апдейт был бы HTTP-запрос.
"""
from __future__ import annotations

import enum
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from app.client import ApiClient, ApiError

log = logging.getLogger(__name__)

ROLE_CACHE_TTL_SECONDS = 30


class Role(str, enum.Enum):
    admin = "admin"
    driver = "driver"
    guest = "guest"


class RoleMiddleware(BaseMiddleware):
    def __init__(self, api: ApiClient | None = None) -> None:
        self._api = api or ApiClient()
        self._cache: dict[int, tuple[float, Role, dict | None]] = {}

    async def _resolve(self, user_id: int) -> tuple[Role, dict | None]:
        cached = self._cache.get(user_id)
        if cached and time.monotonic() - cached[0] < ROLE_CACHE_TTL_SECONDS:
            return cached[1], cached[2]

        try:
            data = await self._api.me(user_id)
            role = Role(data.get("role", "guest"))
            driver = data.get("driver")
        except ApiError as exc:
            # Сервер недоступен — считаем гостем, но не роняем апдейт.
            log.warning("не удалось получить роль %s: %s", user_id, exc)
            return Role.guest, None

        self._cache[user_id] = (time.monotonic(), role, driver)
        return role, driver

    def invalidate(self, user_id: int) -> None:
        """После регистрации/увольнения роль меняется — сбрасываем кэш."""
        self._cache.pop(user_id, None)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        role, driver = Role.guest, None
        if user is not None:
            role, driver = await self._resolve(user.id)

        data["role"] = role
        data["driver"] = driver
        data["api"] = self._api
        data["role_mw"] = self
        return await handler(event, data)
