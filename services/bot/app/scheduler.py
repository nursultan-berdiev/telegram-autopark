"""Фоновые петли бота: опрос алертов и отправка очереди исходящих.

Весь расчёт и расписание — на стороне core-api (celery-beat + periodic_tasks).
Бот — «тупой отправитель»: единственный процесс с доступом в Telegram. Две
инфраструктурные петли (не операторские задачи): `poll_alerts` разносит алерты,
`drain_outbox` шлёт сообщения, которые положили в очередь celery-задачи.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.alerts import poll_alerts
from app.client import ApiClient, ApiError
from app.config import settings

log = logging.getLogger(__name__)

ALERT_POLL_SECONDS = 90
OUTBOX_DRAIN_SECONDS = 30
# Постоянные ошибки доставки: бот заблокирован, чат не найден, невалидный запрос.
# Их бессмысленно ретраить — сдаёмся сразу. Остальное считаем временным.
_PERMANENT_SEND_ERRORS = (TelegramForbiddenError, TelegramBadRequest)


async def _mark_failed(api: ApiClient, message_id: int, *, permanent: bool) -> None:
    try:
        await api.outbox_failed(message_id, permanent=permanent)
    except ApiError as exc:
        log.warning("outbox %s: не отметить неудачу: %s", message_id, exc)


async def drain_outbox(bot: Bot, api: ApiClient) -> int:
    """Отправляет неотправленные сообщения из очереди core-api.

    Семантика at-least-once: отметку `sent` делаем ПОСЛЕ отправки, поэтому при
    сбое отметки сообщение уйдёт повторно на следующем проходе — редкий дубль
    предпочтён потере платёжного уведомления (пара повторов отметки сужает окно).
    Постоянная ошибка (бот заблокирован/нет чата) — сдаёмся сразу; временная —
    счётчик попыток на сервере (лимит как страховка от вечного ретрая).
    Возвращает число отправленных (для логов/тестов).
    """
    try:
        rows = await api.outbox_pending()
    except ApiError as exc:
        log.debug("outbox: core-api недоступен (%s)", exc)
        return 0

    sent = 0
    for msg in rows:
        mid = msg["id"]
        try:
            await bot.send_message(msg["recipient_tg_user_id"], msg["text"])
        except _PERMANENT_SEND_ERRORS as e:
            log.warning("outbox %s: постоянная ошибка (%s) — сдаёмся", mid, e)
            await _mark_failed(api, mid, permanent=True)
            continue
        except Exception as e:  # noqa: BLE001 — временная ошибка: повтор на следующем проходе
            log.warning("outbox %s: временная ошибка доставки: %s", mid, e)
            await _mark_failed(api, mid, permanent=False)
            continue
        sent += 1
        for attempt in range(2):  # at-least-once: сузить окно дубля при сбое отметки
            try:
                await api.outbox_sent(mid)
                break
            except ApiError as exc:
                if attempt == 1:
                    log.warning("outbox %s: доставлено, но отметка не прошла: %s", mid, exc)
    return sent


def setup_scheduler(bot: Bot, api: ApiClient) -> AsyncIOScheduler:
    """Поднимает две инфраструктурные петли. Бизнес-расписания — в celery-beat."""
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(
        poll_alerts,
        IntervalTrigger(seconds=ALERT_POLL_SECONDS),
        args=[bot, api],
        id="poll_alerts",
        replace_existing=True,
    )
    scheduler.add_job(
        drain_outbox,
        IntervalTrigger(seconds=OUTBOX_DRAIN_SECONDS),
        args=[bot, api],
        id="drain_outbox",
        replace_existing=True,
        max_instances=1,  # не допускаем параллельных проходов (двойная отправка)
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "Петли бота: опрос алертов раз в %d с, отправка очереди раз в %d с",
        ALERT_POLL_SECONDS,
        OUTBOX_DRAIN_SECONDS,
    )
    return scheduler
