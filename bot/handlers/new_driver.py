"""Админ: создание приглашения нового водителя (FR-INV-1..5)."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.deep_linking import create_start_link
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import NewDriverCB
from bot.config import settings
from bot.db.models import CarStatus
from bot.filters import IsAdmin
from bot.keyboards.admin import BTN_NEW_DRIVER
from bot.keyboards.drivers import pick_car_kb
from bot.services import cars as cars_service
from bot.services import invitations as invitations_service

router = Router(name="new_driver")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)


@router.message(F.text == BTN_NEW_DRIVER)
async def new_driver_start(message: Message, session: AsyncSession) -> None:
    free_cars = await cars_service.list_free_cars(session)
    if not free_cars:
        await message.answer(
            "Нет свободных машин для нового водителя. "
            "Добавьте машину или освободите занятую."
        )
        return
    await message.answer(
        "Выберите машину для нового водителя (показаны только свободные):",
        reply_markup=pick_car_kb(free_cars),
    )


@router.callback_query(NewDriverCB.filter(F.action == "pick_car"))
async def pick_car(
    query: CallbackQuery, callback_data: NewDriverCB, session: AsyncSession, bot: Bot
) -> None:
    car = await cars_service.get_car(session, callback_data.car_id)
    if car is None:
        await query.answer("Машина не найдена.", show_alert=True)
        return
    # Повторная проверка занятости на момент подтверждения.
    if car.status != CarStatus.free:
        await query.answer("Эта машина уже занята. Выберите другую.", show_alert=True)
        return

    invitation = await invitations_service.create_invitation(
        session, car_id=car.id, created_by=query.from_user.id
    )
    link = await create_start_link(bot, invitation.code)

    title = car.plate + (f" · {car.model}" if car.model else "")
    await query.message.answer(
        f"✅ Приглашение для машины <b>{title}</b> создано.\n\n"
        f"Отправьте водителю эту ссылку (действует "
        f"{settings.invite_ttl_label}, одноразовая):\n{link}"
    )
    await query.answer()
