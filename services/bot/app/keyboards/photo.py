"""Клавиатуры фотоконтроля: сдача водителем, список водителей и осмотров у админа."""
from __future__ import annotations

from datetime import date, datetime

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import PhotoCB
from app.fines_view import LOCAL_TZ

# Одна кнопка на обе роли: водитель сдаёт, админ смотрит.
BTN_PHOTO = "📸 Фотоконтроль"


def due_label(status: dict, today: date) -> str:
    """«просрочен N дн.» / «сегодня» / «до 24.10» — по сроку из core-api."""
    if status["overdue_days"] > 0:
        return f"просрочен {status['overdue_days']} дн."
    due = date.fromisoformat(str(status["next_due_on"]))
    if due == today:
        return "срок сегодня"
    return f"до {due:%d.%m}"


def status_mark(status: dict, today: date) -> str:
    """🔴 просрочен, 🟡 срок сегодня, 🟢 в запасе."""
    if status["overdue_days"] > 0:
        return "🔴"
    return "🟡" if date.fromisoformat(str(status["next_due_on"])) <= today else "🟢"


def start_kb() -> InlineKeyboardMarkup:
    """Кнопка начала сдачи под статусом водителя."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📸 Пройти фотоконтроль", callback_data=PhotoCB(action="start"))
    return builder.as_markup()


def report_kb() -> InlineKeyboardMarkup:
    """Отчёт о состоянии после четырёх фото."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Всё в порядке", callback_data=PhotoCB(action="ok"))
    builder.button(text="⚠️ Есть замечания", callback_data=PhotoCB(action="issues"))
    builder.adjust(1)
    return builder.as_markup()


def drivers_kb(statuses: list[dict], today: date) -> InlineKeyboardMarkup:
    """Список водителей для админа: метка, номер, имя и срок на кнопке."""
    builder = InlineKeyboardBuilder()
    for status in statuses:
        builder.button(
            text=(
                f"{status_mark(status, today)} {status['car_plate']} · "
                f"{status['driver_name']} — {due_label(status, today)}"
            ),
            callback_data=PhotoCB(action="driver", driver_id=status["driver_id"]),
        )
    builder.adjust(1)
    return builder.as_markup()


def inspection_label(inspection: dict) -> str:
    """Дата осмотра по часам парка; ⚠️ — если водитель указал замечания."""
    created = inspection.get("created_at")
    when = (
        f"{datetime.fromisoformat(str(created)).astimezone(LOCAL_TZ):%d.%m.%Y %H:%M}"
        if created
        else f"осмотр #{inspection['id']}"
    )
    return f"⚠️ {when} · есть замечания" if inspection["has_issues"] else f"✅ {when}"


def inspections_kb(inspections: list[dict], driver_id: int) -> InlineKeyboardMarkup:
    """История осмотров водителя и возврат к списку."""
    builder = InlineKeyboardBuilder()
    for inspection in inspections:
        builder.button(
            text=inspection_label(inspection),
            callback_data=PhotoCB(action="open", driver_id=driver_id, insp_id=inspection["id"]),
        )
    builder.button(text="⬅️ К списку водителей", callback_data=PhotoCB(action="drivers"))
    builder.adjust(1)
    return builder.as_markup()
