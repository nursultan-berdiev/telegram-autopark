"""Клавиатуры раздела «Автопарк».

Машины приходят из core-api как dict (CarDTO), не ORM-объекты.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import CarCB, FleetCB

BTN_ADD_CAR = "➕ Добавить машину"

_STATUS_LABEL = {
    "free": "🟢 свободна",
    "occupied": "🔴 занята",
}


def car_status_label(status: str) -> str:
    return _STATUS_LABEL.get(status, str(status))


def cars_list_kb(cars: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for car in cars:
        title = f"{car['plate']}"
        if car.get("model"):
            title += f" · {car['model']}"
        title += f" — {car_status_label(car['status'])}"
        builder.button(text=title, callback_data=CarCB(action="view", car_id=car["id"]))
    builder.adjust(1)
    return builder.as_markup()


def car_detail_kb(car: dict) -> InlineKeyboardMarkup:
    car_id = car["id"]
    builder = InlineKeyboardBuilder()
    builder.button(text="📍 Где машина", callback_data=FleetCB(action="state", car_id=car_id))
    builder.button(text="📡 Трекер", callback_data=FleetCB(action="tracker", car_id=car_id))
    builder.button(text="🧾 Штрафы", callback_data=FleetCB(action="fines", car_id=car_id))
    builder.button(text="🔧 ТО", callback_data=FleetCB(action="maint", car_id=car_id))
    builder.button(text="🗑 Удалить", callback_data=CarCB(action="delete", car_id=car_id))
    builder.button(text="⬅️ К списку", callback_data=CarCB(action="view", car_id=0))
    builder.adjust(2)
    return builder.as_markup()


def car_delete_confirm_kb(car: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить",
        callback_data=CarCB(action="delete_confirm", car_id=car["id"]),
    )
    builder.button(
        text="❌ Отмена", callback_data=CarCB(action="view", car_id=car["id"])
    )
    builder.adjust(2)
    return builder.as_markup()
