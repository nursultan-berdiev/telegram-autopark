"""Документы: по машине и общие по парку. Загружает админ, водитель открывает свои.

Файл не скачиваем: core-api хранит его Telegram file_id, и бот пересылает его
тем же file_id — так водитель получает PDF/фото, которое можно показать ГАИ.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.callbacks import DocCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin, IsAdminOrDriver
from app.fines_view import esc
from app.keyboards.admin import admin_menu
from app.keyboards.documents import (
    BTN_DOCUMENTS,
    COMMON_LABEL,
    admin_list_kb,
    delete_confirm_kb,
    driver_list_kb,
    file_admin_kb,
    owners_kb,
)
from app.middlewares.role import Role
from app.states.document import DocumentForm
from contracts import DOCUMENT_MAX_FILE_NAME, DOCUMENT_MAX_TITLE

log = logging.getLogger(__name__)
router = Router(name="documents")
OWNERS_TEXT = "📄 Документы. Выберите машину или общие документы парка."


def file_size_label(size: int | None) -> str:
    if not size:
        return ""
    if size < 1024 * 1024:
        return f"{max(1, size // 1024)} КБ"
    return f"{size / (1024 * 1024):.1f} МБ"


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Двойной тап по той же кнопке даёт тот же текст — Telegram отвечает ошибкой."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def _owner_title(api: ApiClient, car_id: int) -> str:
    if not car_id:
        return COMMON_LABEL
    car = await api.car(car_id)
    return f"🚗 Документы {esc(car['plate'])}"


async def _documents_of(api: ApiClient, car_id: int) -> list[dict]:
    return await api.common_documents() if not car_id else await api.car_documents(car_id)


async def _show_admin_list(message: Message, api: ApiClient, car_id: int, *, edit: bool) -> None:
    try:
        title = await _owner_title(api, car_id)
        documents = await _documents_of(api, car_id)
    except ApiError as exc:
        await message.answer(exc.human)
        return
    text = f"{title}: {len(documents)}" if documents else f"{title}: документов пока нет."
    markup = admin_list_kb(documents, car_id)
    if edit:
        await _edit(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


# --- списки -----------------------------------------------------------------


@router.message(F.text == BTN_DOCUMENTS, IsAdminOrDriver)
async def open_documents(
    message: Message, api: ApiClient, role: Role, driver: dict | None = None
) -> None:
    if role is Role.admin:
        try:
            cars = await api.cars()
        except ApiError as exc:
            await message.answer(exc.human)
            return
        await message.answer(OWNERS_TEXT, reply_markup=owners_kb(cars))
        return

    # Машина водителя — из его роли, а не из кнопки: callback_data подделывается.
    car_id = (driver or {}).get("car_id")
    try:
        car_docs = await api.car_documents(car_id) if car_id else []
        common_docs = await api.common_documents()
    except ApiError as exc:
        await message.answer(exc.human)
        return
    if not car_docs and not common_docs:
        await message.answer(
            "📄 Документов пока нет. Если нужен документ — обратитесь к владельцу парка."
        )
        return
    plate = (driver or {}).get("car_plate")
    head = f"📄 Документы — машина {esc(plate)}" if plate else "📄 Документы"
    await message.answer(
        f"{head}\nНажмите на документ — бот пришлёт файл, его можно показать ГАИ.",
        reply_markup=driver_list_kb(car_docs, common_docs),
    )


@router.callback_query(DocCB.filter(F.action == "owners"), IsAdmin)
async def show_owners(callback: CallbackQuery, api: ApiClient) -> None:
    try:
        cars = await api.cars()
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await _edit(callback.message, OWNERS_TEXT, owners_kb(cars))
    await callback.answer()


@router.callback_query(DocCB.filter(F.action == "list"), IsAdmin)
async def show_list(callback: CallbackQuery, callback_data: DocCB, api: ApiClient) -> None:
    # Из карточки машины приходим с фото-сообщения: текста у него нет, edit_text упадёт.
    edit = callback.message.text is not None
    await _show_admin_list(callback.message, api, callback_data.car_id, edit=edit)
    await callback.answer()


# --- открыть файл -----------------------------------------------------------


@router.callback_query(DocCB.filter(F.action == "open"), IsAdminOrDriver)
async def open_document(
    callback: CallbackQuery,
    callback_data: DocCB,
    api: ApiClient,
    role: Role,
    driver: dict | None = None,
) -> None:
    try:
        doc = await api.document(callback_data.doc_id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    is_admin = role is Role.admin
    if not is_admin and doc["car_id"] not in (None, (driver or {}).get("car_id")):
        await callback.answer("Документ не найден", show_alert=True)
        return

    caption = f"📄 <b>{esc(doc['title'])}</b>"
    markup = file_admin_kb(doc["id"]) if is_admin else None
    try:
        if doc["file_kind"] == "photo":
            await callback.message.answer_photo(doc["file_id"], caption=caption, reply_markup=markup)
        else:
            await callback.message.answer_document(
                doc["file_id"], caption=caption, reply_markup=markup
            )
    except TelegramBadRequest as exc:
        # file_id привязан к токену бота: после смены бота старые файлы не открыть.
        log.warning("документ %s не отправлен: %s", doc["id"], exc)
        await callback.answer(
            "Файл недоступен в Telegram — его нужно загрузить заново.", show_alert=True
        )
        return
    await callback.answer()


# --- загрузка ---------------------------------------------------------------


@router.callback_query(DocCB.filter(F.action == "add"), IsAdmin)
async def add_start(callback: CallbackQuery, callback_data: DocCB, state: FSMContext) -> None:
    await state.set_state(DocumentForm.title)
    await state.update_data(car_id=callback_data.car_id)
    await callback.message.answer(
        "Как назвать документ? Например: «ОСАГО до 31.12.2026».\n"
        "Для отмены отправьте /cancel."
    )
    await callback.answer()


@router.message(DocumentForm.title, F.text, IsAdmin)
async def add_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not title or len(title) > DOCUMENT_MAX_TITLE:
        await message.answer(f"Название — от 1 до {DOCUMENT_MAX_TITLE} символов. Пришлите снова.")
        return
    await state.update_data(title=title)
    await state.set_state(DocumentForm.file)
    await message.answer("Пришлите файл: PDF, фото или любой другой документ.")


@router.message(DocumentForm.file, F.document | F.photo, IsAdmin)
async def add_file(message: Message, state: FSMContext, api: ApiClient) -> None:
    if message.document is not None:
        file = message.document
        payload = {
            "file_id": file.file_id,
            "file_kind": "document",
            # Имя только для подписи: длинное обрезаем, а не отказываем в загрузке.
            "file_name": (file.file_name or "")[:DOCUMENT_MAX_FILE_NAME] or None,
            "file_size": file.file_size,
        }
    else:
        photo = message.photo[-1]
        payload = {
            "file_id": photo.file_id,
            "file_kind": "photo",
            "file_name": None,
            "file_size": photo.file_size,
        }
    data = await state.get_data()
    car_id = data.get("car_id") or None
    try:
        doc = await api.add_document(
            tg_id=message.from_user.id, car_id=car_id, title=data["title"], **payload
        )
    except ApiError as exc:
        await message.answer(exc.human)
        return
    await state.clear()
    size = file_size_label(doc.get("file_size"))
    await message.answer(
        f"✅ Документ «{esc(doc['title'])}» добавлен" + (f" ({size})." if size else "."),
        reply_markup=admin_menu(),
    )
    await _show_admin_list(message, api, car_id or 0, edit=False)


@router.message(DocumentForm.file, IsAdmin)
async def add_file_invalid(message: Message) -> None:
    await message.answer("Нужен файл: пришлите документ (PDF и др.) или фото.")


# --- удаление ---------------------------------------------------------------


@router.callback_query(DocCB.filter(F.action == "delete"), IsAdmin)
async def ask_delete(callback: CallbackQuery, callback_data: DocCB) -> None:
    await callback.message.edit_reply_markup(reply_markup=delete_confirm_kb(callback_data.doc_id))
    await callback.answer("Удалить документ? Водители больше не смогут его открыть")


@router.callback_query(DocCB.filter(F.action == "delete_cancel"), IsAdmin)
async def cancel_delete(callback: CallbackQuery, callback_data: DocCB) -> None:
    await callback.message.edit_reply_markup(reply_markup=file_admin_kb(callback_data.doc_id))
    await callback.answer()


@router.callback_query(DocCB.filter(F.action == "delete_confirm"), IsAdmin)
async def delete(callback: CallbackQuery, callback_data: DocCB, api: ApiClient) -> None:
    try:
        await api.delete_document(callback_data.doc_id, tg_id=callback.from_user.id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Документ удалён")
    await callback.message.answer("🗑 Документ удалён — у водителей его больше нет.")
