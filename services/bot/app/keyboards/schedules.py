"""Клавиатуры настройки графиков платежей.

Водители приходят из core-api как dict (DriverDTO), не ORM-объекты.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import ScheduleCB

# Периодичность — просто строковый код, который core-api понимает как есть
# (SchedulePeriod живёт только в core-api, у бота своего домена больше нет).
_PERIODS = (
    ("daily", "Ежедневно"),
    ("weekly", "Еженедельно"),
    ("monthly", "Ежемесячно"),
    ("custom", "Произвольно (N дней)"),
)


def drivers_list_kb(drivers: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for d in drivers:
        car = d.get("car_plate") or "без машины"
        builder.button(
            text=f"{d['full_name']} · {car}",
            callback_data=ScheduleCB(action="pick_driver", driver_id=d["id"]),
        )
    builder.adjust(1)
    return builder.as_markup()


def period_kb(driver_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for period, label in _PERIODS:
        builder.button(
            text=label,
            callback_data=ScheduleCB(
                action="set_period", driver_id=driver_id, value=period
            ),
        )
    builder.adjust(2)
    return builder.as_markup()


def start_date_kb(driver_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Сегодня",
        callback_data=ScheduleCB(action="start_today", driver_id=driver_id),
    )
    builder.button(
        text="Завтра",
        callback_data=ScheduleCB(action="start_tomorrow", driver_id=driver_id),
    )
    builder.adjust(2)
    return builder.as_markup()
