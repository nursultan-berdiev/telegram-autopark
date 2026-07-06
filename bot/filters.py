"""Фильтры доступа по роли (используют data["role"] из RoleMiddleware)."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from bot.middlewares.role import Role


class RoleFilter(BaseFilter):
    def __init__(self, *roles: Role) -> None:
        self.roles = roles

    async def __call__(self, event: TelegramObject, role: Role) -> bool:
        return role in self.roles


IsAdmin = RoleFilter(Role.admin)
IsDriver = RoleFilter(Role.driver)
IsGuest = RoleFilter(Role.guest)
