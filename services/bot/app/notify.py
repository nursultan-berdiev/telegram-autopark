"""Уведомление водителя о блокировке/разблокировке двигателя.

Вынесено отдельно от роутера-хендлера, чтобы опрос алертов (`app.alerts`) мог
звать его при автоматическом срабатывании взведённой блокировки, не завязываясь
на модуль с хендлерами.
"""
from __future__ import annotations

import logging

from aiogram import Bot

from app.client import ApiClient, ApiError

log = logging.getLogger(__name__)

DRIVER_BLOCKED_TEXT = (
    "Двигатель вашего автомобиля {plate} заблокирован администратором парка. "
    "Свяжитесь с парком, чтобы решить вопрос."
)
DRIVER_UNBLOCKED_TEXT = "Двигатель вашего автомобиля {plate} разблокирован."

# Авто-блокировка за неоплату: это сообщение шлётся в момент РЕАЛЬНОЙ глушилки
# (fire_armed на безопасной остановке) — отдельно от уведомления «блок включён»
# в 22:00, поэтому формулировка «только что заглушён».
DRIVER_OVERDUE_BLOCKED_TEXT = (
    "⛔ Двигатель автомобиля {plate} только что заглушён из-за неоплаты аренды. "
    "Внесите оплату — двигатель разблокируется автоматически."
)
DRIVER_OVERDUE_UNBLOCKED_TEXT = (
    "Оплата получена. Двигатель вашего автомобиля {plate} разблокирован."
)

# Авто-блокировка за неоплаченные штрафы.
DRIVER_FINES_BLOCKED_TEXT = (
    "Двигатель вашего автомобиля {plate} заблокирован из-за неоплаченных штрафов. "
    "Погасите штрафы — двигатель разблокируется автоматически."
)
DRIVER_FINES_UNBLOCKED_TEXT = (
    "Штрафы погашены. Двигатель вашего автомобиля {plate} разблокирован."
)


async def notify_driver(bot: Bot, api: ApiClient, car_id: int, text_template: str) -> None:
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
        await bot.send_message(tg_user_id, text_template.format(plate=car.get("plate", "")))
    except Exception as e:  # noqa: BLE001 — недоставленное уведомление не отменяет команду
        log.warning("не удалось уведомить водителя %s: %s", tg_user_id, e)
