"""Клавиатуры для приглашения водителей и регистрации.

Водители/машины приходят из core-api как dict (DriverDTO/CarDTO), не ORM.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import DriverCB, NewDriverCB


def drivers_list_kb(drivers: list[dict], *, fired: bool) -> InlineKeyboardMarkup:
    """Список водителей: работающие или архив уволенных."""
    builder = InlineKeyboardBuilder()
    for d in drivers:
        car = d.get("car_plate") or ("уволен" if fired else "без машины")
        builder.button(
            text=f"{d['full_name']} · {car}",
            callback_data=DriverCB(action="view", driver_id=d["id"]),
        )
    if fired:
        builder.button(
            text="⬅️ Работающие", callback_data=DriverCB(action="list_active")
        )
    else:
        builder.button(text="🗂 Уволенные", callback_data=DriverCB(action="list_fired"))
    builder.adjust(1)
    return builder.as_markup()


def driver_card_kb(driver: dict) -> InlineKeyboardMarkup:
    """Карточка водителя. Уволить можно только действующего."""
    builder = InlineKeyboardBuilder()
    active = driver.get("active", True)
    if active:
        builder.button(
            text="🚫 Уволить", callback_data=DriverCB(action="fire", driver_id=driver["id"])
        )
    builder.button(
        text="⬅️ К списку",
        callback_data=DriverCB(action="list_fired" if not active else "list_active"),
    )
    builder.adjust(1)
    return builder.as_markup()


def fire_confirm_kb(driver: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, уволить",
        callback_data=DriverCB(action="fire_confirm", driver_id=driver["id"]),
    )
    builder.button(
        text="Отмена", callback_data=DriverCB(action="view", driver_id=driver["id"])
    )
    builder.adjust(1)
    return builder.as_markup()


def pick_car_kb(free_cars: list[dict]) -> InlineKeyboardMarkup:
    """Выбор свободной машины для нового приглашения (только свободные — FR-INV-2)."""
    builder = InlineKeyboardBuilder()
    for car in free_cars:
        title = car["plate"] + (f" · {car['model']}" if car.get("model") else "")
        builder.button(
            text=title, callback_data=NewDriverCB(action="pick_car", car_id=car["id"])
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
