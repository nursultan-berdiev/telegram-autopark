"""Фоновые задачи бота: напоминания и опрос алертов.

Считает всё core-api; бот только тянет план и доставляет сообщения —
у сервера нет канала в Telegram (plan/03, plan/06).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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


async def send_overdue_warnings(
    bot: Bot, api: ApiClient, now: datetime | None = None, *, force: bool = False
) -> dict[str, int]:
    """Предупреждение водителю за 15 мин до срока (если аренда не оплачена)."""
    return await _send_driver_notices(
        bot, api, api.reminders_overdue_warning, api.reminders_mark_warning, now, force=force
    )


async def send_block_notices(
    bot: Bot, api: ApiClient, now: datetime | None = None, *, force: bool = False
) -> dict[str, int]:
    """Уведомление водителю в 22:00: блокировка двигателя за аренду включена."""
    return await _send_driver_notices(
        bot, api, api.reminders_block_notice, api.reminders_mark_block_notice, now, force=force
    )


async def _send_driver_notices(
    bot: Bot,
    api: ApiClient,
    fetch: Callable[..., Awaitable[dict]],
    mark: Callable[[list[int]], Awaitable[None]],
    now: datetime | None,
    *,
    force: bool,
) -> dict[str, int]:
    now = now or datetime.now(ZoneInfo(settings.timezone))
    try:
        plan = await fetch(now, force=force)
    except ApiError as exc:
        log.warning("уведомления водителям: core-api недоступен (%s)", exc)
        return {"drivers": 0}

    sent: list[int] = []
    for notice in plan.get("notices", []):
        try:
            await bot.send_message(notice["tg_user_id"], notice["text"])
            sent.append(notice["schedule_id"])
        except Exception as e:  # noqa: BLE001 — один водитель не должен ронять рассылку
            log.warning("не удалось уведомить водителя %s: %s", notice["tg_user_id"], e)
    if sent:
        try:
            await mark(sent)
        except ApiError as exc:
            log.warning("не удалось отметить уведомления: %s", exc)
    return {"drivers": len(sent)}


async def send_admin_digest(
    bot: Bot, api: ApiClient, now: datetime | None = None
) -> dict[str, int]:
    """Вечерняя сводка владельцу: кто оплатил/нет, автоблокировки."""
    try:
        data = await api.reminders_admin_digest(now)
    except ApiError as exc:
        log.warning("админ-дайджест: core-api недоступен (%s)", exc)
        return {"owners": 0}
    text = data.get("text")
    if not text:
        return {"owners": 0}
    owners = 0
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
            owners += 1
        except Exception as e:  # noqa: BLE001
            log.warning("не удалось отправить дайджест админу %s: %s", admin_id, e)
    return {"owners": owners}


def _parse_hm(value: str) -> tuple[int, int]:
    """'ЧЧ:ММ' → (час, минута). Формат уже проверен в Settings (`_valid_hm`),
    поэтому здесь просто разбор — без молчаливой подмены на полночь."""
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour, minute


def _cron_at(hour: int, minute: int) -> CronTrigger:
    """CronTrigger с ЯВНОЙ зоной парка. APScheduler 3.x НЕ навешивает зону
    планировщика на уже созданный экземпляр триггера — без timezone= он берёт
    локальную зону контейнера (UTC), и задача уедет на несколько часов (у нас
    21:45/22:00/22:05 стреляли бы в UTC = ~04:00 по Бишкеку)."""
    return CronTrigger(hour=hour, minute=minute, timezone=ZoneInfo(settings.timezone))


def setup_scheduler(bot: Bot, api: ApiClient) -> AsyncIOScheduler:
    """Планировщик стартует всегда: опрос алертов не зависит от напоминаний.

    Утреннее напоминание — под REMINDERS_ENABLED; каждая вечерняя задача у срока —
    под своим флагом (независимое отключение)."""
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))

    # Опрос алертов — безусловно (раньше гас вместе с напоминаниями).
    scheduler.add_job(
        poll_alerts,
        IntervalTrigger(seconds=ALERT_POLL_SECONDS),
        args=[bot, api],
        id="poll_alerts",
        replace_existing=True,
    )

    if settings.reminders_enabled:
        scheduler.add_job(
            send_daily_reminders,
            _cron_at(settings.reminder_hour, 0),
            args=[bot, api],
            id="daily_reminders",
            replace_existing=True,
        )
    else:
        log.info("Утренние напоминания отключены (REMINDERS_ENABLED=0)")

    # Вечерние уведомления у срока 22:00 — три независимо отключаемые задачи.
    evening = [
        ("payment_warn", settings.payment_warn_enabled, settings.payment_warn_at, send_overdue_warnings),
        ("payment_block_notice", settings.payment_block_notice_enabled, settings.payment_block_notice_at, send_block_notices),
        ("payment_digest", settings.payment_digest_enabled, settings.payment_digest_at, send_admin_digest),
    ]
    enabled_jobs = []
    for job_id, enabled, at, fn in evening:
        if not enabled:
            continue
        hour, minute = _parse_hm(at)
        scheduler.add_job(
            fn,
            _cron_at(hour, minute),
            args=[bot, api],
            id=job_id,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=300,
        )
        enabled_jobs.append(f"{job_id}@{hour:02d}:{minute:02d}")

    scheduler.start()
    log.info(
        "Планировщик (%s): опрос алертов раз в %d с; вечерние задачи: %s",
        settings.timezone,
        ALERT_POLL_SECONDS,
        ", ".join(enabled_jobs) or "нет",
    )
    return scheduler
