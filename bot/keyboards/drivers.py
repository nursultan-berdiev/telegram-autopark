"""Клавиатуры для приглашения водителей и регистрации."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import NewDriverCB
from bot.db.models import Car


def pick_car_kb(free_cars: list[Car]) -> InlineKeyboardMarkup:
    """Выбор свободной машины для нового приглашения (только свободные — FR-INV-2)."""
    builder = InlineKeyboardBuilder()
    for car in free_cars:
        title = car.plate + (f" · {car.model}" if car.model else "")
        builder.button(
            text=title, callback_data=NewDriverCB(action="pick_car", car_id=car.id)
        )
    builder.adjust(1)
    return builder.as_markup()


def share_phone_kb() -> ReplyKeyboardMarkup:
    """Кнопка «Поделиться номером» для шага телефона."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
