"""Клавиатуры администратора."""
from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# Тексты кнопок главного меню админа. По мере реализации этапов
# соответствующие хендлеры будут подключаться к этим кнопкам.
BTN_CARS = "🚗 Автопарк"
BTN_NEW_DRIVER = "➕ Новый водитель"
BTN_SCHEDULES = "📅 Графики платежей"
BTN_REPORTS = "📊 Отчёты"
BTN_AI = "🤖 Спросить ИИ"


def admin_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_CARS)
    builder.button(text=BTN_NEW_DRIVER)
    builder.button(text=BTN_SCHEDULES)
    builder.button(text=BTN_REPORTS)
    builder.button(text=BTN_AI)
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)
