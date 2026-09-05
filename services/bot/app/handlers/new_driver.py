"""Админ: создание приглашения нового водителя (FR-INV-1..5)."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.deep_linking import create_start_link

from app.callbacks import NewDriverCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin
from app.keyboards.admin import BTN_NEW_DRIVER
from app.keyboards.drivers import pick_car_kb

router = Router(name="new_driver")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)


@router.message(F.text == BTN_NEW_DRIVER)
async def new_driver_start(message: Message, api: ApiClient) -> None:
    free_cars = await api.cars(free=True)
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
    query: CallbackQuery, callback_data: NewDriverCB, api: ApiClient, bot: Bot
) -> None:
    try:
        car = await api.car(callback_data.car_id)
    except ApiError as exc:
        await query.answer(exc.human, show_alert=True)
        return
    # Повторная проверка занятости на момент подтверждения.
    if car["status"] != "free":
        await query.answer("Эта машина уже занята. Выберите другую.", show_alert=True)
        return

    try:
        invitation = await api.create_invitation(car["id"], created_by=query.from_user.id)
    except ApiError as exc:
        await query.answer(exc.human, show_alert=True)
        return

    link = await create_start_link(bot, invitation["code"])

    title = car["plate"] + (f" · {car['model']}" if car.get("model") else "")
    await query.message.answer(
        f"✅ Приглашение для машины <b>{title}</b> создано.\n\n"
        f"Отправьте водителю эту ссылку (действует "
        f"{invitation['ttl_label']}, одноразовая):\n{link}"
    )
    await query.answer()
