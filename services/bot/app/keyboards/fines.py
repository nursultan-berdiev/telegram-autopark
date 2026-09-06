"""Клавиатура списка штрафов: страница кнопок плюс стрелки.

Данные штрафа живут на кнопке, а не в тексте: так номер постановления
открывается одним касанием, а сообщение остаётся шапкой.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import FineCB
from app.fines_view import COLUMNS, fine_button_label, page_slice


def fines_page(
    fines: list[dict],
    *,
    scope: str,
    ref_id: int = 0,
    page: int = 0,
    with_plate: bool = False,
    # Любая фабрика callback_data: из карточки машины сюда приходит FleetCB,
    # а функции нужен только .pack().
    extra: list[tuple[str, CallbackData]] | None = None,
) -> tuple[InlineKeyboardMarkup, int, int]:
    """Клавиатура одной страницы. Возвращает её вместе с номером и числом страниц."""
    shown, index, pages = page_slice(fines, page)

    builder = InlineKeyboardBuilder()
    if index > 0:
        builder.button(
            text="◀ Назад",
            callback_data=FineCB(
                action="page", scope=scope, ref_id=ref_id, page=index - 1
            ),
        )
    for fine in shown:
        builder.button(
            text=fine_button_label(fine, with_plate=with_plate),
            callback_data=FineCB(
                action="card",
                fine_id=fine["id"],
                scope=scope,
                ref_id=ref_id,
                page=index,
            ),
        )
    if index < pages - 1:
        builder.button(
            text="Далее ▶",
            callback_data=FineCB(
                action="page", scope=scope, ref_id=ref_id, page=index + 1
            ),
        )
    # Ровно два столбца: три ряда по две ячейки — это и есть страница.
    builder.adjust(COLUMNS)
    for text, callback in extra or []:
        # Отдельным рядом под страницей: это действия над списком, а не штрафы.
        builder.row(InlineKeyboardButton(text=text, callback_data=callback.pack()))
    return builder.as_markup(), index, pages
