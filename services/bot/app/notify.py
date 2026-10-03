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
