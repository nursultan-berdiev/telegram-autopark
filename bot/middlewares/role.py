"""Middleware, определяющий роль пользователя для каждого апдейта.

Роли (см. ФТ, раздел 2):
- admin      — id есть в ADMIN_IDS (переменная окружения);
- driver     — зарегистрированный водитель (есть запись в drivers);
- guest      — все остальные (без прав; доступ только к сценарию по ссылке).

Результат кладётся в data["role"] и data["driver"], доступен хендлерам
и фильтрам. Также прокидывается открытая сессия БД в data["session"].
"""
from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User
from sqlalchemy import select

from bot.config import settings
from bot.db.base import async_session_maker
from bot.db.models import Driver


class Role(str, enum.Enum):
    admin = "admin"
    driver = "driver"
    guest = "guest"


class RoleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        async with async_session_maker() as session:
            data["session"] = session

            role = Role.guest
            driver: Driver | None = None

            if user is not None:
                if settings.is_admin(user.id):
                    role = Role.admin
                else:
                    driver = await session.scalar(
                        select(Driver).where(Driver.tg_user_id == user.id)
                    )
                    if driver is not None:
                        role = Role.driver

            data["role"] = role
            data["driver"] = driver

            return await handler(event, data)
