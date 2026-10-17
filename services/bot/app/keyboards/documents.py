"""Клавиатуры документов: выбор машины, список документов, удаление."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import DocCB

# Одна кнопка на обе роли: админ выбирает машину, водитель сразу видит свои.
BTN_DOCUMENTS = "📄 Документы"
COMMON_LABEL = "🏢 Общие документы парка"
# Название до 128 символов целиком не влезает в кнопку; полное — в подписи к файлу.
LABEL_LEN = 50


def _short(title: str) -> str:
    return title if len(title) <= LABEL_LEN else title[: LABEL_LEN - 1] + "…"


def owners_kb(cars: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=COMMON_LABEL, callback_data=DocCB(action="list", car_id=0))
    for car in cars:
        title = car["plate"] + (f" · {car['model']}" if car.get("model") else "")
        builder.button(text=f"🚗 {title}", callback_data=DocCB(action="list", car_id=car["id"]))
    builder.adjust(1)
    return builder.as_markup()


def admin_list_kb(documents: list[dict], car_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for doc in documents:
        builder.button(
            text=f"📄 {_short(doc['title'])}",
            callback_data=DocCB(action="open", car_id=car_id, doc_id=doc["id"]),
        )
    builder.button(text="➕ Добавить документ", callback_data=DocCB(action="add", car_id=car_id))
    builder.button(text="⬅️ Назад", callback_data=DocCB(action="owners"))
    builder.adjust(1)
    return builder.as_markup()


def driver_list_kb(car_docs: list[dict], common_docs: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for doc in car_docs:
        builder.button(
            text=f"🚗 {_short(doc['title'])}", callback_data=DocCB(action="open", doc_id=doc["id"])
        )
    for doc in common_docs:
        builder.button(
            text=f"🏢 {_short(doc['title'])}", callback_data=DocCB(action="open", doc_id=doc["id"])
        )
    builder.adjust(1)
    return builder.as_markup()


def file_admin_kb(doc_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить документ", callback_data=DocCB(action="delete", doc_id=doc_id))
    return builder.as_markup()


def delete_confirm_kb(doc_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить", callback_data=DocCB(action="delete_confirm", doc_id=doc_id)
    )
    builder.button(text="❌ Отмена", callback_data=DocCB(action="delete_cancel", doc_id=doc_id))
    builder.adjust(2)
    return builder.as_markup()
