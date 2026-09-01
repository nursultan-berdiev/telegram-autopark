"""Действия по карточке алерта: блокировка двигателя и разбор системных.

Блокировку инициирует только человек и только по алерту — автономной
immobilization в системе нет (plan/06).
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import AlertCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin

log = logging.getLogger(__name__)

router = Router(name="alerts")

_GATE_HINT = "Блокировка отложена: {reason}. Повторите, когда машина встанет."

DRIVER_BLOCKED_TEXT = (
    "Двигатель вашего автомобиля {plate} заблокирован администратором парка. "
    "Свяжитесь с парком, чтобы решить вопрос."
)
DRIVER_UNBLOCKED_TEXT = "Двигатель вашего автомобиля {plate} разблокирован."


async def _notify_driver(bot: Bot, api: ApiClient, car_id: int, text_template: str) -> None:
    """Водитель обязан узнать о блокировке — это и UX, и юридика аренды."""
    try:
        car = await api.car(car_id)
    except ApiError as exc:
        log.warning("не удалось получить машину %s для уведомления: %s", car_id, exc)
        return

    driver_id = car.get("driver_id")
    if not driver_id:
        return
    try:
        driver = await api.driver(driver_id)
    except ApiError as exc:
        log.warning("не удалось получить водителя %s: %s", driver_id, exc)
        return

    payload = driver.get("driver", driver)
    tg_user_id = payload.get("tg_user_id")
    if not tg_user_id:
        return
    try:
        await bot.send_message(
            tg_user_id, text_template.format(plate=car.get("plate", ""))
        )
    except Exception as e:  # noqa: BLE001 — недоставленное уведомление не отменяет команду
        log.warning("не удалось уведомить водителя %s: %s", tg_user_id, e)


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
    elif status == "blocked_by_safety":
        await callback.message.answer(
            _GATE_HINT.format(reason=result.get("reason") or "машина не готова")
        )
    else:
        await callback.message.answer(
            f"Команда не прошла: {result.get('reason') or 'ошибка адаптера'}"
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
