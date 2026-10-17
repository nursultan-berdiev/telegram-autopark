"""Фотоконтроль состояния машины: водитель сдаёт 4 фото и отчёт, админ смотрит.

Срок, напоминания и алерт о просрочке ведёт core-api (задача celery-beat). Бот
собирает фото (Telegram file_id), отдаёт осмотр в core-api и сразу показывает
его админам — как чек и селфи.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message

from app.callbacks import PhotoCB
from app.client import ApiClient, ApiError
from app.config import settings
from app.filters import IsAdmin, IsAdminOrDriver, IsDriver
from app.fines_view import LOCAL_TZ, esc, moment
from app.keyboards.driver_menu import driver_menu
from app.keyboards.photo import (
    BTN_PHOTO,
    drivers_kb,
    inspections_kb,
    report_kb,
    start_kb,
)
from app.middlewares.role import Role
from app.states.photo import PhotoForm
from contracts import PHOTO_COMMENT_MAX

log = logging.getLogger(__name__)
router = Router(name="photo")

# Ракурсы по порядку: состояние FSM, поле осмотра, подпись к фото, подсказка.
STEPS = [
    (PhotoForm.front, "front_file_id", "Спереди",
     "Шаг 1 из 4. Сфотографируйте машину спереди — целиком, с номером."),
    (PhotoForm.rear, "rear_file_id", "Сзади",
     "Шаг 2 из 4. Теперь сзади — тоже целиком, с номером."),
    (PhotoForm.interior_front, "interior_front_file_id", "Салон спереди",
     "Шаг 3 из 4. Салон спереди: руль, панель, передние сиденья."),
    (PhotoForm.interior_rear, "interior_rear_file_id", "Салон сзади",
     "Шаг 4 из 4. Салон сзади: задние сиденья."),
]
_STEP_STATES = [step[0] for step in STEPS]


def _today() -> date:
    return datetime.now(LOCAL_TZ).date()


def driver_status_text(status: dict, today: date) -> str:
    due = date.fromisoformat(str(status["next_due_on"]))
    lines = [f"📸 Фотоконтроль машины {esc(status['car_plate'])}"]
    if status["overdue_days"] > 0:
        lines.append(f"⚠️ Просрочен на {status['overdue_days']} дн. Пройдите его сегодня.")
    elif due <= today:
        lines.append("Срок — сегодня.")
    else:
        lines.append(f"Следующий — до {due:%d.%m.%Y}.")
    last = moment(status.get("last_inspection_at"))
    if last:
        lines.append(f"Последний пройден: {last}")
    lines.append("Нужно 4 фото: спереди, сзади, салон спереди, салон сзади — и пара слов о состоянии.")
    return "\n".join(lines)


def album(inspection: dict) -> list[InputMediaPhoto]:
    return [
        InputMediaPhoto(media=inspection[field], caption=label)
        for _, field, label, _ in STEPS
    ]


def report_text(inspection: dict) -> str:
    plate = inspection.get("car_plate") or "машина удалена"
    lines = [
        f"📸 <b>Фотоконтроль · {esc(plate)}</b>",
        f"Водитель: {esc(inspection.get('driver_name') or '—')}",
    ]
    when = moment(inspection.get("created_at"))
    if when:
        lines.append(when)
    if inspection["has_issues"]:
        lines.append(f"⚠️ Есть замечания: {esc(inspection.get('comment') or '')}")
    else:
        lines.append("✅ Замечаний нет")
    return "\n".join(lines)


async def _notify_admins(bot: Bot, inspection: dict) -> None:
    """Админ видит осмотр сразу; недоставка не отменяет сдачу — осмотр есть в разделе."""
    for admin_id in settings.admin_ids:
        try:
            await bot.send_media_group(admin_id, media=album(inspection))
            await bot.send_message(admin_id, report_text(inspection))
        except Exception as e:  # noqa: BLE001 — один админ не должен ронять остальных
            log.warning("осмотр %s не показан админу %s: %s", inspection["id"], admin_id, e)


# --- вход в раздел ----------------------------------------------------------


@router.message(F.text == BTN_PHOTO, IsAdminOrDriver)
async def open_photo(
    message: Message, api: ApiClient, role: Role, driver: dict | None = None
) -> None:
    if role is Role.admin:
        await _show_drivers(message, api, edit=False)
        return
    try:
        status = await api.driver_photo_status((driver or {})["id"])
    except ApiError as exc:
        if exc.status_code == 404:
            await message.answer("За вами не закреплена машина — фотоконтроль не нужен.")
            return
        await message.answer(exc.human)
        return
    await message.answer(driver_status_text(status, _today()), reply_markup=start_kb())


# --- сдача водителем --------------------------------------------------------


@router.callback_query(PhotoCB.filter(F.action == "start"), IsDriver)
async def start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PhotoForm.front)
    await callback.message.answer(f"{STEPS[0][3]}\nДля отмены отправьте /cancel.")
    await callback.answer()


@router.message(StateFilter(*_STEP_STATES), F.photo, IsDriver)
async def take_photo(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    index = [s.state for s in _STEP_STATES].index(getattr(current, "state", current))
    photo = message.photo[-1]
    # file_unique_id одинаков у одного и того же снимка, даже пересланного: так ловим
    # одно фото на двух ракурсах раньше, чем это отвергнет core-api.
    seen = (await state.get_data()).get("seen_photos", [])
    if photo.file_unique_id in seen:
        await message.answer("Это фото уже было на прошлом шаге — снимите этот ракурс отдельно.")
        return
    await state.update_data(
        **{STEPS[index][1]: photo.file_id}, seen_photos=[*seen, photo.file_unique_id]
    )
    if index + 1 < len(STEPS):
        await state.set_state(STEPS[index + 1][0])
        await message.answer(STEPS[index + 1][3])
        return
    await state.set_state(PhotoForm.report)
    await message.answer("Есть ли новые повреждения или неисправности?", reply_markup=report_kb())


@router.message(StateFilter(*_STEP_STATES), IsDriver)
async def take_photo_invalid(message: Message) -> None:
    await message.answer("Нужна фотография: снимите камерой и отправьте как фото, не файлом.")


async def _submit(
    message: Message,
    state: FSMContext,
    api: ApiClient,
    bot: Bot,
    *,
    tg_id: int,
    has_issues: bool,
    comment: str | None,
) -> None:
    data = await state.get_data()
    photos = {field: data[field] for _, field, _, _ in STEPS}
    # Сбрасываем до запроса: второе нажатие иначе сдало бы осмотр дважды.
    await state.clear()
    try:
        result = await api.submit_photo_inspection(
            tg_id=tg_id, has_issues=has_issues, comment=comment, **photos
        )
    except ApiError as exc:
        await state.set_state(PhotoForm.report)
        await state.update_data(**photos)
        await message.answer(
            f"Не удалось сдать фотоконтроль: {exc.human}\nПопробуйте ещё раз:",
            reply_markup=report_kb(),
        )
        return
    due = date.fromisoformat(str(result["next_due_on"]))
    await message.answer(
        f"✅ Фотоконтроль принят. Следующий — до {due:%d.%m.%Y}.", reply_markup=driver_menu()
    )
    await _notify_admins(bot, result["inspection"])


@router.callback_query(PhotoCB.filter(F.action == "ok"), PhotoForm.report, IsDriver)
async def report_ok(
    callback: CallbackQuery, state: FSMContext, api: ApiClient, bot: Bot
) -> None:
    await callback.answer()
    await _submit(
        callback.message, state, api, bot,
        tg_id=callback.from_user.id, has_issues=False, comment=None,
    )


@router.callback_query(PhotoCB.filter(F.action == "issues"), PhotoForm.report, IsDriver)
async def report_issues(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PhotoForm.comment)
    await callback.message.answer(
        "Опишите замечания одним сообщением: что и где повреждено или не работает."
    )
    await callback.answer()


@router.message(PhotoForm.comment, F.text, IsDriver)
async def take_comment(message: Message, state: FSMContext, api: ApiClient, bot: Bot) -> None:
    text = message.text.strip()
    if not text or len(text) > PHOTO_COMMENT_MAX:
        await message.answer(f"Нужно описание до {PHOTO_COMMENT_MAX} символов. Пришлите снова.")
        return
    await _submit(
        message, state, api, bot, tg_id=message.from_user.id, has_issues=True, comment=text
    )


# --- просмотр админом -------------------------------------------------------


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Двойной тап по той же кнопке даёт тот же текст — Telegram отвечает ошибкой."""
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def _show_drivers(message: Message, api: ApiClient, *, edit: bool) -> None:
    try:
        statuses = await api.photo_statuses()
    except ApiError as exc:
        await message.answer(exc.human)
        return
    if not statuses:
        text, markup = "📸 Нет работающих водителей с машиной.", None
    else:
        overdue = sum(1 for s in statuses if s["overdue_days"] > 0)
        text = f"📸 Фотоконтроль: водителей {len(statuses)}, просрочено {overdue}."
        markup = drivers_kb(statuses, _today())
    if edit:
        await _edit(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(PhotoCB.filter(F.action == "drivers"), IsAdmin)
async def show_drivers(callback: CallbackQuery, api: ApiClient) -> None:
    await _show_drivers(callback.message, api, edit=True)
    await callback.answer()


@router.callback_query(PhotoCB.filter(F.action == "driver"), IsAdmin)
async def show_driver(callback: CallbackQuery, callback_data: PhotoCB, api: ApiClient) -> None:
    try:
        inspections = await api.photo_inspections(callback_data.driver_id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    if inspections:
        head = inspections[0]
        text = (
            f"📸 {esc(head.get('car_plate') or '')} · {esc(head.get('driver_name') or '')}\n"
            f"Последние осмотры: {len(inspections)}"
        )
    else:
        text = "📸 Этот водитель ещё не проходил фотоконтроль."
    await _edit(callback.message, text, inspections_kb(inspections, callback_data.driver_id))
    await callback.answer()


@router.callback_query(PhotoCB.filter(F.action == "open"), IsAdmin)
async def open_inspection(callback: CallbackQuery, callback_data: PhotoCB, api: ApiClient) -> None:
    try:
        inspection = await api.photo_inspection(callback_data.insp_id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.message.answer_media_group(album(inspection))
    await callback.message.answer(report_text(inspection))
    await callback.answer()
