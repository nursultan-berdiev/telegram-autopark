"""Общие команды: отмена текущего сценария."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.admin import admin_menu
from bot.middlewares.role import Role

router = Router(name="common")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, role: Role) -> None:
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.answer("Нечего отменять.")
        return
    markup = admin_menu() if role is Role.admin else None
    await message.answer("Действие отменено.", reply_markup=markup)
