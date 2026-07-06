"""Обработка /start и разветвление по ролям.

Этап 0: базовое приветствие для админа и водителя, нейтральный отказ
для гостя (FR-ACC-1/2). Логика deep-link приглашений подключается на
Этапе 2 (обработка `/start <code>`).
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.db.models import Driver
from bot.keyboards.admin import admin_menu
from bot.keyboards.driver_menu import driver_menu
from bot.middlewares.role import Role

router = Router(name="start")


# Deep-link /start <code> обрабатывается в handlers/registration.py
# (роутер registration подключается раньше). Здесь — обычный /start.
@router.message(CommandStart())
async def start(message: Message, role: Role, driver: Driver | None) -> None:
    if role is Role.admin:
        await message.answer(
            "👋 Добро пожаловать, администратор!\n\n"
            "Управляйте автопарком, водителями и платежами через меню ниже.",
            reply_markup=admin_menu(),
        )
    elif role is Role.driver and driver is not None:
        await message.answer(
            f"👋 Здравствуйте, {driver.full_name}!\n"
            "Вы можете вносить платежи и отслеживать свой график.",
            reply_markup=driver_menu(),
        )
    else:
        # Гость без приглашения — нейтральный отказ, без раскрытия деталей.
        await message.answer(
            "У вас нет доступа к этому боту. "
            "Обратитесь к владельцу автопарка за приглашением."
        )
