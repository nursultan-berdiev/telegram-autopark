"""Доставка алертов админам: опрос core-api и карточка под тип алерта.

Кнопка «Заблокировать» уместна только у алертов от правил: на
command_unconfirmed она провоцировала бы повтор блокировки (plan/05).
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.callbacks import AlertCB
from app.client import ApiClient, ApiError
from app.config import settings
from app.fines_view import esc
from app.keyboards.fines import fines_page
from app.notify import (
    DRIVER_BLOCKED_TEXT,
    DRIVER_OVERDUE_BLOCKED_TEXT,
    DRIVER_OVERDUE_UNBLOCKED_TEXT,
    notify_driver,
)

log = logging.getLogger(__name__)

RULE_TYPES = {"overdue_payment", "fines_count", "maintenance_km"}
# Типы алертов, при доставке которых надо уведомить ещё и водителя — и каким текстом.
DRIVER_NOTIFY_TEXT = {
    "armed_block_fired": DRIVER_BLOCKED_TEXT,
    "overdue_block_fired": DRIVER_OVERDUE_BLOCKED_TEXT,
    "overdue_unblock": DRIVER_OVERDUE_UNBLOCKED_TEXT,
}
# Карточки с кнопкой «Разблокировать»: авто-сработавший блок (ручной взвод и за неоплату).
UNBLOCK_CARD_TYPES = {"armed_block_fired", "overdue_block_fired"}

_SEVERITY_MARK = {"info": "•", "warning": "!", "critical": "!!"}


def alert_text(alert: dict) -> str:
    mark = _SEVERITY_MARK.get(alert.get("severity", "warning"), "!")
    plate = alert.get("car_plate") or f"машина #{alert.get('car_id')}"
    body = alert.get("text") or alert.get("type", "")
    # Экранируем: parse_mode=HTML включён глобально, а в тексте алерта
    # встречается всё, что прислал внешний сервис. Одна угловая скобка — и
    # Telegram отвергнет сообщение целиком, то есть алерт просто пропадёт.
    return f"{mark} {esc(plate)}: {esc(body)}"


def alert_keyboard(alert: dict) -> InlineKeyboardMarkup:
    """Кнопки строго по типу алерта."""
    alert_id = int(alert["id"])
    car_id = int(alert["car_id"])
    atype = alert.get("type", "")

    if atype in RULE_TYPES:
        rows = [
            [
                InlineKeyboardButton(
                    text="Заблокировать двигатель",
                    callback_data=AlertCB(
                        action="block", alert_id=alert_id, car_id=car_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отложить",
                    callback_data=AlertCB(action="ack", alert_id=alert_id).pack(),
                ),
            ]
        ]
    elif atype in UNBLOCK_CARD_TYPES:
        # Блок сработал — двигатель заглушён; предлагаем сразу разблокировать.
        rows = [
            [
                InlineKeyboardButton(
                    text="Разблокировать двигатель",
                    callback_data=AlertCB(
                        action="unblock", alert_id=alert_id, car_id=car_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Понятно",
                    callback_data=AlertCB(action="ack", alert_id=alert_id).pack(),
                ),
            ]
        ]
    elif atype == "command_unconfirmed":
        # Повторяем ИМЕННО ту команду, которая не подтвердилась: у алерта о
        # неподтверждённой разблокировке кнопка «Повторить» не должна глушить.
        failed_type = (alert.get("payload") or {}).get("command_type", "engine_stop")
        retry_action = "unblock" if failed_type == "engine_resume" else "retry"
        rows = [
            [
                InlineKeyboardButton(
                    text="Повторить",
                    callback_data=AlertCB(
                        action=retry_action, alert_id=alert_id, car_id=car_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Понятно",
                    callback_data=AlertCB(action="ack", alert_id=alert_id).pack(),
                ),
            ]
        ]
    elif atype == "odometer_untrusted":
        rows = [
            [
                InlineKeyboardButton(
                    text="ТО выполнено",
                    callback_data=AlertCB(
                        action="maint_done", alert_id=alert_id, car_id=car_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Понятно",
                    callback_data=AlertCB(action="ack", alert_id=alert_id).pack(),
                ),
            ]
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text="Понятно",
                    callback_data=AlertCB(action="ack", alert_id=alert_id).pack(),
                )
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def new_fine_card(alert: dict, api: ApiClient) -> tuple[str, InlineKeyboardMarkup]:
    """Шапка и страница кнопок: сами штрафы дозапрашиваем по id из payload.

    Суммы в payload не кладём — они менялись бы в базе, а в старом сообщении
    оставались бы прежними.
    """
    payload = alert.get("payload") or {}
    fine_ids = payload.get("fine_ids") or []
    fines: list[dict] = []
    if fine_ids:
        try:
            by_id = {f["id"]: f for f in await api.fines(int(alert["car_id"]))}
            fines = [by_id[i] for i in fine_ids if i in by_id]
        except ApiError as exc:
            # Уведомление важнее украшений: покажем текст без кнопок.
            log.warning("штрафы алерта %s не прочитаны: %s", alert["id"], exc)

    if not fines:
        return alert_text(alert), alert_keyboard(alert)
    markup, _, _ = fines_page(
        fines, scope="alert", ref_id=int(alert["id"]), page=0
    )
    return alert_text(alert), markup


async def poll_alerts(bot: Bot, api: ApiClient) -> int:
    """Доставляет админам ещё не отправленные алерты — ровно один раз.

    Факт доставки хранит core-api (`notified_at`), а не память бота: поэтому
    после перезапуска уже показанные алерты НЕ рассылаются повторно (пугающие
    дубли «двигатель заблокирован»), и жать «Понятно» для этого не нужно.
    """
    try:
        alerts = await api.pending_alerts()
    except ApiError as exc:
        log.debug("опрос алертов: %s", exc)
        return 0

    delivered = 0
    for alert in alerts:
        alert_id = int(alert["id"])
        if alert.get("type") == "new_fine":
            text, markup = await new_fine_card(alert, api)
        else:
            text, markup = alert_text(alert), alert_keyboard(alert)
        shown = 0
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, text, reply_markup=markup)
                shown += 1
            except Exception as e:  # noqa: BLE001 — один админ не должен ронять рассылку
                log.warning("не удалось показать алерт %s админу %s: %s", alert_id, admin_id, e)

        if not shown:
            # Ни одному админу не дошло — не помечаем доставленным, повторим на
            # следующем проходе (лучше показать позже, чем потерять).
            log.error("алерт %s не дошёл ни до кого — повторим на следующем проходе", alert_id)
            continue

        # Фиксируем доставку на сервере. Если отметить не удалось — НЕ уведомляем
        # водителя и не считаем доставленным: алерт вернётся в pending и уйдёт на
        # следующем проходе. Иначе водитель получал бы «двигатель заблокирован» на
        # каждом проходе, пока отметка не пройдёт (админам редкий повтор допустим).
        try:
            await api.mark_alert_notified(alert_id)
        except ApiError as exc:
            log.error("не удалось отметить алерт %s доставленным: %s", alert_id, exc)
            continue
        delivered += shown
        # Авто-сработавшая блокировка/разблокировка — водитель узнаёт в этот момент.
        driver_text = DRIVER_NOTIFY_TEXT.get(alert.get("type", ""))
        if driver_text is not None:
            await notify_driver(bot, api, int(alert["car_id"]), driver_text)

    return delivered
