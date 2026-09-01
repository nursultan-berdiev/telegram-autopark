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

log = logging.getLogger(__name__)

RULE_TYPES = {"overdue_payment", "fines_count", "maintenance_km"}

_SEVERITY_MARK = {"info": "•", "warning": "!", "critical": "!!"}

# Какие алерты уже показали — чтобы не слать одно и то же каждые полторы минуты.
_delivered: set[int] = set()


def alert_text(alert: dict) -> str:
    mark = _SEVERITY_MARK.get(alert.get("severity", "warning"), "!")
    plate = alert.get("car_plate") or f"машина #{alert.get('car_id')}"
    body = alert.get("text") or alert.get("type", "")
    return f"{mark} {plate}: {body}"


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


async def poll_alerts(bot: Bot, api: ApiClient) -> int:
    """Показывает админам новые открытые алерты. Возвращает число доставленных."""
    try:
        alerts = await api.alerts(status="open")
    except ApiError as exc:
        log.debug("опрос алертов: %s", exc)
        return 0

    delivered = 0
    open_ids = set()
    for alert in alerts:
        alert_id = int(alert["id"])
        open_ids.add(alert_id)
        if alert_id in _delivered:
            continue
        markup = alert_keyboard(alert)
        text = alert_text(alert)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, text, reply_markup=markup)
                delivered += 1
            except Exception as e:  # noqa: BLE001 — один админ не должен ронять рассылку
                log.warning("не удалось показать алерт %s админу %s: %s", alert_id, admin_id, e)
        _delivered.add(alert_id)

    # Закрытые алерты можно показать снова, если они откроются заново.
    _delivered.intersection_update(open_ids)
    return delivered
