"""Действия по карточке алерта: блокировка двигателя и разбор системных.

Блокировку инициирует только человек и только по алерту — автономной
immobilization в системе нет (plan/06).
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import AlertCB, ArmCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin
from app.notify import DRIVER_BLOCKED_TEXT, DRIVER_UNBLOCKED_TEXT, notify_driver

log = logging.getLogger(__name__)

router = Router(name="alerts")

# Уведомление водителя живёт в app.notify (его же зовёт опрос алертов при
# автосрабатывании взвода). Имя сохраняем для ссылок внутри модуля и тестов.
_notify_driver = notify_driver


@router.callback_query(AlertCB.filter(F.action.in_({"block", "retry"})), IsAdmin)
async def block_engine(
    callback: CallbackQuery, callback_data: AlertCB, api: ApiClient
) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)  # против двойного тапа
    try:
        result = await api.command(
            callback_data.car_id,
            type="engine_block",
            requested_by=callback.from_user.id,
            alert_id=callback_data.alert_id or None,
            arm_if_unsafe=True,  # едет/офлайн — не отказываем, а взводим
        )
    except ApiError as exc:
        await callback.message.answer(f"Не удалось заблокировать: {exc.human}")
        await callback.answer()
        return

    command = result.get("command", {})
    status = command.get("status")
    if result.get("ok"):
        builder = InlineKeyboardBuilder()
        builder.button(
            text="Разблокировать двигатель",
            callback_data=AlertCB(
                action="unblock", alert_id=callback_data.alert_id, car_id=callback_data.car_id
            ),
        )
        await callback.message.answer(
            "Команда на блокировку отправлена. Жду подтверждения телеметрией.",
            reply_markup=builder.as_markup(),
        )
        await _notify_driver(
            callback.bot, api, callback_data.car_id, DRIVER_BLOCKED_TEXT
        )
    elif status == "armed":
        # Машина в движении/офлайн — блокировка взведена. Водителя пока НЕ
        # уведомляем: узнает в момент фактического срабатывания.
        builder = InlineKeyboardBuilder()
        builder.button(
            text="Отменить ожидание",
            callback_data=ArmCB(
                action="cancel", car_id=callback_data.car_id, cmd_id=command.get("id") or 0
            ),
        )
        await callback.message.answer(
            "⏳ Блокировка взведена. Двигатель заглушится автоматически, как "
            "только машина встанет (стоит, зажигание выключено).",
            reply_markup=builder.as_markup(),
        )
    else:
        await callback.message.answer(
            f"Команда не прошла: {result.get('reason') or 'ошибка адаптера'}"
        )
    await callback.answer()


@router.callback_query(AlertCB.filter(F.action == "block_ask"), IsAdmin)
async def block_ask(
    callback: CallbackQuery, callback_data: AlertCB, api: ApiClient
) -> None:
    """Подтверждение перед ручной блокировкой с карточки машины (без алерта).

    Блокировка с карточки машины — «холодное» действие без повода-алерта,
    поэтому спрашиваем лишний раз. Сама команда уходит из block_engine по
    кнопке «Да» (action="block", alert_id=0 → блок без привязки к алерту).
    """
    try:
        car = await api.car(callback_data.car_id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return

    plate = car.get("plate") or f"#{callback_data.car_id}"
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, заблокировать",
        callback_data=AlertCB(action="block", alert_id=0, car_id=callback_data.car_id),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=AlertCB(action="cancel", alert_id=0, car_id=callback_data.car_id),
    )
    builder.adjust(1)
    await callback.message.answer(
        f"Заблокировать двигатель {plate}? Если машина стоит с выключенным "
        "зажиганием — заглушим сразу; если едет — заблокируем автоматически, "
        "как только встанет.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(AlertCB.filter(F.action == "cancel"), IsAdmin)
async def cancel_action(
    callback: CallbackQuery, callback_data: AlertCB
) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отменено")


@router.callback_query(ArmCB.filter(F.action == "cancel"), IsAdmin)
async def cancel_armed_block(
    callback: CallbackQuery, callback_data: ArmCB, api: ApiClient
) -> None:
    """Снять взведённую (ожидающую) блокировку, пока она не сработала."""
    try:
        result = await api.cancel_command(
            callback_data.car_id, callback_data.cmd_id, requested_by=callback.from_user.id
        )
    except ApiError as exc:
        await callback.message.answer(f"Не удалось отменить: {exc.human}")
        await callback.answer()
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    if result.get("ok"):
        await callback.message.answer("Ожидание блокировки отменено.")
    else:
        await callback.message.answer(
            result.get("reason") or "Блокировка уже не в ожидании."
        )
    await callback.answer()


@router.callback_query(AlertCB.filter(F.action == "unblock"), IsAdmin)
async def unblock_engine(
    callback: CallbackQuery, callback_data: AlertCB, api: ApiClient
) -> None:
    try:
        result = await api.command(
            callback_data.car_id,
            type="engine_unblock",
            requested_by=callback.from_user.id,
            alert_id=callback_data.alert_id or None,
        )
    except ApiError as exc:
        await callback.message.answer(f"Не удалось разблокировать: {exc.human}")
        await callback.answer()
        return

    if result.get("ok"):
        await callback.message.answer("Команда на разблокировку отправлена.")
        await _notify_driver(
            callback.bot, api, callback_data.car_id, DRIVER_UNBLOCKED_TEXT
        )
    else:
        await callback.message.answer(
            f"Команда не прошла: {result.get('reason') or 'ошибка адаптера'}"
        )
    await callback.answer()


@router.callback_query(AlertCB.filter(F.action == "ack"), IsAdmin)
async def ack_alert(
    callback: CallbackQuery, callback_data: AlertCB, api: ApiClient
) -> None:
    try:
        await api.ack_alert(callback_data.alert_id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отложено")


@router.callback_query(AlertCB.filter(F.action == "maint_done"), IsAdmin)
async def maintenance_done(
    callback: CallbackQuery, callback_data: AlertCB, api: ApiClient
) -> None:
    """База пробега переустанавливается тем же действием, что и отметка ТО."""
    try:
        await api.maintenance_done(
            callback_data.car_id, "oil", tg_id=callback.from_user.id
        )
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("ТО отмечено, база пробега переустановлена.")
    await callback.answer()
