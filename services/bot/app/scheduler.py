"""Фоновые задачи бота: напоминания и опрос алертов.

Считает всё core-api; бот только тянет план и доставляет сообщения —
у сервера нет канала в Telegram (plan/03, plan/06).
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.alerts import poll_alerts
from app.client import ApiClient, ApiError
from app.config import settings

log = logging.getLogger(__name__)

ALERT_POLL_SECONDS = 90


async def send_daily_reminders(
    bot: Bot, api: ApiClient, now: datetime | None = None, *, force: bool = False
) -> dict[str, int]:
    """Один прогон рассылки. Возвращает счётчики (для логов и тестов).

    force=True — обойти антиспам «раз в день» (ручной прогон /remind_now force).
    """
    tzinfo = ZoneInfo(settings.timezone)
    now = now or datetime.now(tzinfo)

    try:
        plan = await api.reminders_plan(now, force=force)
    except ApiError as exc:
        log.warning("напоминания: core-api недоступен (%s)", exc)
        return {"drivers": 0, "owners": 0}

    sent: list[int] = []
    for reminder in plan.get("reminders", []):
        try:
            await bot.send_message(reminder["tg_user_id"], reminder["text"])
            sent.append(reminder["schedule_id"])
        except Exception as e:  # noqa: BLE001 — один водитель не должен ронять рассылку
            log.warning("Не удалось напомнить водителю %s: %s", reminder["tg_user_id"], e)

    if sent:
        try:
            await api.reminders_mark(sent)
        except ApiError as exc:
            log.warning("не удалось отметить напоминания: %s", exc)

    owners = 0
    digest = plan.get("owner_digest") or []
    if digest:
        text = "\n".join(digest)
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, text)
                owners += 1
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось отправить сводку админу %s: %s", admin_id, e)

    return {"drivers": len(sent), "owners": owners}


def setup_scheduler(bot: Bot, api: ApiClient) -> AsyncIOScheduler | None:
    if not settings.reminders_enabled:
        log.info("Напоминания отключены (REMINDERS_ENABLED=0)")
        return None

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(
        send_daily_reminders,
        CronTrigger(hour=settings.reminder_hour, minute=0),
        args=[bot, api],
        id="daily_reminders",
        replace_existing=True,
    )

    scheduler.add_job(
        poll_alerts,
        IntervalTrigger(seconds=ALERT_POLL_SECONDS),
        args=[bot, api],
        id="poll_alerts",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "Планировщик: напоминания в %02d:00 %s, опрос алертов раз в %d с",
        settings.reminder_hour,
        settings.timezone,
        ALERT_POLL_SECONDS,
    )
    return scheduler
