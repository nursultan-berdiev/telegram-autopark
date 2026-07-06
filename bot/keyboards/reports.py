"""Клавиатура раздела «Отчёты»."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callbacks import ReportCB


def reports_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚗 Машины и водители", callback_data=ReportCB(kind="cars"))
    builder.button(text="⏰ Кому скоро платить", callback_data=ReportCB(kind="upcoming"))
    builder.button(
        text="📄 Выписка по водителям", callback_data=ReportCB(kind="by_driver")
    )
    builder.button(text="📄 Выписка по машинам", callback_data=ReportCB(kind="by_car"))
    builder.adjust(1)
    return builder.as_markup()
