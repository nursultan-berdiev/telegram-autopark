"""Ежедневная рассылка напоминаний (APScheduler).

Бот до этого был чисто реактивным — просыпался только на апдейт. Здесь
появляется единственная фоновая задача: раз в сутки в REMINDER_HOUR по TZ
разослать напоминания водителям и сводку владельцу.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.config import settings
from bot.db.base import async_session_maker
from bot.services import reminders as reminders_service

log = logging.getLogger(__name__)


async def send_daily_reminders(
    bot: Bot, now: datetime | None = None, *, force: bool = False
) -> dict[str, int]:
    """Один прогон рассылки. Возвращает счётчики (для логов и тестов).

    force=True — игнорировать анти-спам (ручной прогон командой /remind_now).
    """
    tzinfo = ZoneInfo(settings.timezone)
    now = now or datetime.now(tzinfo)
    today = now.astimezone(tzinfo).date()

    async with async_session_maker() as session:
        plan = await reminders_service.collect(
            session, now, settings.timezone, force=force
        )

        sent: list[int] = []
        for reminder in plan.reminders:
            try:
                await bot.send_message(reminder.tg_user_id, reminder.text)
                sent.append(reminder.schedule_id)
            except Exception as e:  # noqa: BLE001 — один водитель не должен ронять рассылку
                log.warning(
                    "Не удалось напомнить водителю %s: %s", reminder.tg_user_id, e
                )

        # Помечаем только тех, кому реально доставили.
        await reminders_service.mark_reminded(session, sent, today)

    digest = plan.owner_digest()
    owners = 0
    if digest:
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, digest)
                owners += 1
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось отправить сводку админу %s: %s", admin_id, e)

    log.info(
        "Напоминания: водителям %d, сводок владельцу %d", len(sent), owners
    )
    return {"drivers": len(sent), "owners": owners}


def setup_scheduler(bot: Bot) -> AsyncIOScheduler | None:
    """Заводит ежедневную рассылку. None — если напоминания выключены."""
    if not settings.reminders_enabled:
        log.info("Напоминания выключены (REMINDERS_ENABLED=0).")
        return None

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(
        send_daily_reminders,
        CronTrigger(hour=settings.reminder_hour, minute=0),
        args=[bot],
        id="daily_reminders",
        # Если бот лежал в момент срабатывания — отправим при старте, но не
        # накопленной пачкой: анти-спам по last_reminded_on всё равно защитит.
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "Напоминания включены: ежедневно в %02d:00 (%s).",
        settings.reminder_hour,
        settings.timezone,
    )
    return scheduler
