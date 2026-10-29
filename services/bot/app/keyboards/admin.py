"""Клавиатуры администратора."""
from __future__ import annotations

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from app.keyboards.announcements import BTN_ANNOUNCEMENTS
from app.keyboards.documents import BTN_DOCUMENTS
from app.keyboards.photo import BTN_PHOTO

# Тексты кнопок главного меню админа. По мере реализации этапов
# соответствующие хендлеры будут подключаться к этим кнопкам.
BTN_CARS = "🚗 Автопарк"
BTN_DRIVERS = "👤 Водители"
BTN_NEW_DRIVER = "➕ Новый водитель"
BTN_SCHEDULES = "📅 Графики платежей"
BTN_REPORTS = "📊 Отчёты"
BTN_AI = "🤖 Спросить ИИ"
BTN_FINES = "🧾 Штрафы"
BTN_ADMIN_WEB = "⚙️ Админка"
# Кнопка выхода из разговора с ИИ. НЕ входит в ADMIN_MENU_BUTTONS: её обрабатывает
# сам ai_query в состоянии разговора, а не middleware выхода по кнопке меню.
BTN_AI_EXIT = "🚪 Выйти"

# Все кнопки главного меню. Нажатие любой из них завершает разговор с ИИ
# (см. ExitAiConversationMiddleware): владелец ушёл в другой раздел.
ADMIN_MENU_BUTTONS = frozenset(
    {
        BTN_CARS,
        BTN_DRIVERS,
        BTN_NEW_DRIVER,
        BTN_SCHEDULES,
        BTN_REPORTS,
        BTN_AI,
        BTN_FINES,
        BTN_ANNOUNCEMENTS,
        BTN_DOCUMENTS,
        BTN_PHOTO,
        BTN_ADMIN_WEB,
    }
)


def _menu_buttons(builder: ReplyKeyboardBuilder) -> None:
    builder.button(text=BTN_CARS)
    builder.button(text=BTN_DRIVERS)
    builder.button(text=BTN_NEW_DRIVER)
    builder.button(text=BTN_SCHEDULES)
    builder.button(text=BTN_REPORTS)
    builder.button(text=BTN_AI)
    builder.button(text=BTN_FINES)
    builder.button(text=BTN_ANNOUNCEMENTS)
    builder.button(text=BTN_DOCUMENTS)
    builder.button(text=BTN_PHOTO)
    builder.button(text=BTN_ADMIN_WEB)


def admin_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    _menu_buttons(builder)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def ai_conversation_menu() -> ReplyKeyboardMarkup:
    """Клавиатура во время разговора с ИИ: меню + «Выйти» первой строкой.

    Меню остаётся: нажатие любого раздела завершает разговор и открывает раздел
    (middleware гасит состояние). «Выйти» — закончить разговор и вернуться в меню.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text=BTN_AI_EXIT)
    _menu_buttons(builder)
    # Первая строка — одна кнопка «Выйти», ниже меню по две.
    builder.adjust(1, 2)
    return builder.as_markup(resize_keyboard=True)
