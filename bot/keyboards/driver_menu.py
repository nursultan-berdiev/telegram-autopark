"""Меню водителя и клавиатуры платежей."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.callbacks import PaymentCB

BTN_PAY = "💳 Оплатить"
BTN_MY_SCHEDULE = "📅 Мой график"


def driver_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_PAY)
    builder.button(text=BTN_MY_SCHEDULE)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def confirm_payment_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Всё верно", callback_data=PaymentCB(action="confirm"))
    builder.button(text="🔄 Отправить заново", callback_data=PaymentCB(action="retry"))
    builder.adjust(1)
    return builder.as_markup()
