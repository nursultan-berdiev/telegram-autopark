"""Клавиатуры настройки графиков платежей."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import ScheduleCB
from bot.db.models import Driver, SchedulePeriod


def drivers_list_kb(drivers: list[Driver]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for d in drivers:
        car = d.car.plate if d.car else "без машины"
        builder.button(
            text=f"{d.full_name} · {car}",
            callback_data=ScheduleCB(action="pick_driver", driver_id=d.id),
        )
    builder.adjust(1)
    return builder.as_markup()


def period_kb(driver_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for period, label in (
        (SchedulePeriod.daily, "Ежедневно"),
        (SchedulePeriod.weekly, "Еженедельно"),
        (SchedulePeriod.monthly, "Ежемесячно"),
        (SchedulePeriod.custom, "Произвольно (N дней)"),
    ):
        builder.button(
            text=label,
            callback_data=ScheduleCB(
                action="set_period", driver_id=driver_id, value=period.value
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
