"""Клавиатуры объявлений: страница истории, карточка, предпросмотр."""
from __future__ import annotations

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import AnnouncementCB
from app.fines_view import LOCAL_TZ

# Одна кнопка на обе роли: история у админа и водителей общая.
BTN_ANNOUNCEMENTS = "📢 Объявления"

PAGE_SIZE = 5
LABEL_LEN = 40


def button_label(announcement: dict) -> str:
    """Дата и начало первой строки — по ним объявление узнают в списке."""
    lines = announcement["text"].strip().splitlines() or [""]
    head = lines[0]
    if len(head) > LABEL_LEN:
        head = head[: LABEL_LEN - 1] + "…"
    created = announcement.get("created_at")
    if not created:
        return head
    # API отдаёт UTC: объявление от 02:00 по Бишкеку иначе попало бы во вчера.
    when = datetime.fromisoformat(str(created)).astimezone(LOCAL_TZ)
    return f"{when:%d.%m} · {head}"


def history_page(
    announcements: list[dict], *, page: int, is_admin: bool
) -> tuple[InlineKeyboardMarkup | None, int, int]:
    """Клавиатура страницы истории + номер страницы и их число."""
    pages = max(1, -(-len(announcements) // PAGE_SIZE))
    index = max(0, min(page, pages - 1))
    shown = announcements[index * PAGE_SIZE : (index + 1) * PAGE_SIZE]

    builder = InlineKeyboardBuilder()
    for item in shown:
        builder.row(
            InlineKeyboardButton(
                text=button_label(item),
                callback_data=AnnouncementCB(
                    action="card", ann_id=item["id"], page=index
                ).pack(),
            )
        )
    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=AnnouncementCB(action="page", page=index - 1).pack(),
            )
        )
    if index < pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="Далее ▶",
                callback_data=AnnouncementCB(action="page", page=index + 1).pack(),
            )
        )
    if nav:
        builder.row(*nav)
    if is_admin:
        builder.row(
            InlineKeyboardButton(
                text="➕ Новое объявление",
                callback_data=AnnouncementCB(action="new").pack(),
            )
        )
    markup = builder.as_markup()
    return (markup if markup.inline_keyboard else None), index, pages


def card_kb(announcement_id: int, page: int, *, is_admin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_admin:
        builder.button(
            text="🗑 Удалить",
            callback_data=AnnouncementCB(action="delete", ann_id=announcement_id, page=page),
        )
    builder.button(text="⬅️ К списку", callback_data=AnnouncementCB(action="page", page=page))
    builder.adjust(1)
    return builder.as_markup()


def delete_confirm_kb(announcement_id: int, page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить из истории",
        callback_data=AnnouncementCB(
            action="delete_confirm", ann_id=announcement_id, page=page
        ),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=AnnouncementCB(action="card", ann_id=announcement_id, page=page),
    )
    builder.adjust(1)
    return builder.as_markup()


def preview_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить всем водителям", callback_data=AnnouncementCB(action="send"))
    builder.button(text="❌ Отмена", callback_data=AnnouncementCB(action="cancel"))
    builder.adjust(1)
    return builder.as_markup()
