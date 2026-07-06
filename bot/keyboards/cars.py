"""Клавиатуры раздела «Автопарк»."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import CarCB
from bot.db.models import Car, CarStatus

BTN_ADD_CAR = "➕ Добавить машину"

_STATUS_LABEL = {
    CarStatus.free: "🟢 свободна",
    CarStatus.occupied: "🔴 занята",
}


def car_status_label(status: CarStatus) -> str:
    return _STATUS_LABEL.get(status, str(status))


def cars_list_kb(cars: list[Car]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for car in cars:
        title = f"{car.plate}"
        if car.model:
            title += f" · {car.model}"
        title += f" — {car_status_label(car.status)}"
        builder.button(text=title, callback_data=CarCB(action="view", car_id=car.id))
    builder.adjust(1)
    return builder.as_markup()


def car_detail_kb(car: Car) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑 Удалить", callback_data=CarCB(action="delete", car_id=car.id)
    )
    builder.button(text="⬅️ К списку", callback_data=CarCB(action="view", car_id=0))
    builder.adjust(2)
    return builder.as_markup()


def car_delete_confirm_kb(car: Car) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить",
        callback_data=CarCB(action="delete_confirm", car_id=car.id),
    )
    builder.button(
        text="❌ Отмена", callback_data=CarCB(action="view", car_id=car.id)
    )
    builder.adjust(2)
    return builder.as_markup()
