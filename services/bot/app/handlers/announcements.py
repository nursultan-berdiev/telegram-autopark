"""Объявления парка: история для всех, публикация и удаление — для админа.

Рассылку водителям делает core-api через очередь исходящих (её шлёт петля
drain_outbox), поэтому бот здесь только собирает текст и показывает историю.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.callbacks import AnnouncementCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin, IsAdminOrDriver
from app.fines_view import esc, moment
from app.keyboards.admin import admin_menu
from app.keyboards.announcements import (
    BTN_ANNOUNCEMENTS,
    card_kb,
    delete_confirm_kb,
    history_page,
    preview_kb,
)
from app.middlewares.role import Role
from app.states.announcement import AnnouncementForm
from contracts import ANNOUNCEMENT_MAX_TEXT, render_announcement, telegram_length

log = logging.getLogger(__name__)
router = Router(name="announcements")


def list_text(announcements: list[dict], index: int, pages: int) -> str:
    if not announcements:
        return "📢 Объявлений пока нет."
    head = f"📢 Объявления: {len(announcements)}"
    if pages > 1:
        head += f"\nстраница {index + 1}/{pages}"
    return head


def card_text(announcement: dict, *, is_admin: bool) -> str:
    when = moment(announcement.get("created_at")) or ""
    text = f"📢 <b>Объявление от {when}</b>\n\n{esc(announcement['text'])}"
    if is_admin:
        text += f"\n\nРазослано водителям: {announcement.get('recipients', 0)}"
    return text


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Двойной тап по той же кнопке даёт тот же текст — Telegram отвечает ошибкой."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def _show_history(
    message: Message, api: ApiClient, *, is_admin: bool, page: int = 0, edit: bool = False
) -> None:
    try:
        announcements = await api.announcements()
    except ApiError as exc:
        await message.answer(exc.human)
        return
    markup, index, pages = history_page(announcements, page=page, is_admin=is_admin)
    text = list_text(announcements, index, pages)
    if edit:
        await _edit(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


# --- история ----------------------------------------------------------------


@router.message(F.text == BTN_ANNOUNCEMENTS, IsAdminOrDriver)
async def open_history(message: Message, api: ApiClient, role: Role) -> None:
    await _show_history(message, api, is_admin=role is Role.admin)


@router.callback_query(AnnouncementCB.filter(F.action == "page"), IsAdminOrDriver)
async def turn_page(
    callback: CallbackQuery, callback_data: AnnouncementCB, api: ApiClient, role: Role
) -> None:
    await _show_history(
        callback.message, api, is_admin=role is Role.admin, page=callback_data.page, edit=True
    )
    await callback.answer()


@router.callback_query(AnnouncementCB.filter(F.action == "card"), IsAdminOrDriver)
async def show_card(
    callback: CallbackQuery, callback_data: AnnouncementCB, api: ApiClient, role: Role
) -> None:
    try:
        announcement = await api.announcement(callback_data.ann_id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    is_admin = role is Role.admin
    await _edit(
        callback.message,
        card_text(announcement, is_admin=is_admin),
        card_kb(announcement["id"], callback_data.page, is_admin=is_admin),
    )
    await callback.answer()


# --- публикация -------------------------------------------------------------


@router.callback_query(AnnouncementCB.filter(F.action == "new"), IsAdmin)
async def new_announcement(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AnnouncementForm.text)
    await callback.message.answer(
        "Напишите текст объявления одним сообщением — его получат все водители.\n"
        "Для отмены отправьте /cancel."
    )
    await callback.answer()


@router.message(AnnouncementForm.text, F.text, IsAdmin)
async def take_text(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text:
        await message.answer("Пустое объявление — пришлите текст.")
        return
    # Проверяем до предпросмотра: длиннее предела Telegram его не отправит вовсе.
    length = telegram_length(text)
    if length > ANNOUNCEMENT_MAX_TEXT:
        await message.answer(
            f"Слишком длинно: {length} символов, можно до {ANNOUNCEMENT_MAX_TEXT}. "
            "Сократите и пришлите снова."
        )
        return
    await state.update_data(text=text)
    await state.set_state(AnnouncementForm.confirm)
    await message.answer("Так объявление увидят водители:")
    await message.answer(render_announcement(text), reply_markup=preview_kb())


@router.message(AnnouncementForm.text, IsAdmin)
async def take_text_invalid(message: Message) -> None:
    await message.answer("Нужен текст. Фото и файлы к объявлению не прикрепляются.")


@router.callback_query(
    AnnouncementCB.filter(F.action == "send"), AnnouncementForm.confirm, IsAdmin
)
async def send(callback: CallbackQuery, state: FSMContext, api: ApiClient) -> None:
    text = (await state.get_data()).get("text") or ""
    # Сбрасываем до запроса: второе нажатие иначе разослало бы объявление дважды.
    await state.clear()
    try:
        announcement = await api.publish_announcement(text=text, tg_id=callback.from_user.id)
    except ApiError as exc:
        await state.set_state(AnnouncementForm.confirm)
        await state.update_data(text=text)
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    recipients = announcement.get("recipients", 0)
    if recipients:
        done = f"✅ Объявление опубликовано. Разошлём {recipients} водителям в течение минуты."
    else:
        done = "✅ Объявление сохранено в истории. Активных водителей нет — рассылать некому."
    await callback.message.answer(done, reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(
    AnnouncementCB.filter(F.action == "cancel"), AnnouncementForm.confirm, IsAdmin
)
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Объявление не отправлено.", reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(AnnouncementCB.filter(F.action.in_({"send", "cancel"})), IsAdmin)
async def preview_stale(callback: CallbackQuery) -> None:
    """Кнопка со старого предпросмотра: он уже отправлен или отменён. Чужой
    сценарий (например, добавление машины) при этом не сбрасываем."""
    await callback.answer(
        "Этот предпросмотр уже не действует — создайте объявление заново.", show_alert=True
    )


# --- удаление ---------------------------------------------------------------


@router.callback_query(AnnouncementCB.filter(F.action == "delete"), IsAdmin)
async def ask_delete(callback: CallbackQuery, callback_data: AnnouncementCB) -> None:
    await callback.message.edit_reply_markup(
        reply_markup=delete_confirm_kb(callback_data.ann_id, callback_data.page)
    )
    await callback.answer(
        "Ещё не ушедшая рассылка остановится; у кого сообщение уже есть, у того останется"
    )


@router.callback_query(AnnouncementCB.filter(F.action == "delete_confirm"), IsAdmin)
async def delete(callback: CallbackQuery, callback_data: AnnouncementCB, api: ApiClient) -> None:
    try:
        await api.delete_announcement(callback_data.ann_id, tg_id=callback.from_user.id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.answer("Удалено из истории")
    await _show_history(
        callback.message, api, is_admin=True, page=callback_data.page, edit=True
    )
