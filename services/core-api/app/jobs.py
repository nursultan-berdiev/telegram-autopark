"""Фоновые задачи core-api.

Досрочивание команд обязательно: при молчащем трекере подтверждение по
биту 27 не придёт никогда, и без джобы команда навсегда осталась бы
«отправленной», а админ не узнал бы о провале (plan/06).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import timedelta

from app.config import settings
from app.db.base import async_session_maker
from app.domain import commands as commands_domain
from app.domain import admin_auth
from app.domain import periodic as periodic_domain
from app.domain import telemetry as telemetry_domain
from app.rules import engine

log = logging.getLogger(__name__)

COMMAND_SWEEP_SECONDS = 60
CLEANUP_INTERVAL_HOURS = 24
# Больше жёсткого лимита задачи в celery (30 мин): всё, что живёт дольше,
# уже не выполняется.
STALE_RUN_AFTER = timedelta(minutes=45)
STALE_RUN_SWEEP_MINUTES = 15


async def run_rules() -> None:
    async with async_session_maker() as session:
        triggered = await engine.evaluate_all(session)
        if triggered:
            log.info("правила: сработало %d", triggered)


async def sweep_commands() -> None:
    async with async_session_maker() as session:
        unconfirmed = await commands_domain.sweep_unconfirmed(session)
        if unconfirmed:
            log.warning("команд без подтверждения: %d", unconfirmed)


async def cleanup_telemetry() -> None:
    async with async_session_maker() as session:
        removed = await telemetry_domain.cleanup(session)
        if removed:
            log.info("телеметрия: удалено %d старых точек", removed)


async def cleanup_login_tokens() -> None:
    """Использованные и просроченные ссылки в админку иначе копятся вечно."""
    async with async_session_maker() as session:
        removed = await admin_auth.purge_expired(session)
        if removed:
            log.info("админка: удалено %d просроченных ссылок входа", removed)


async def close_stale_runs() -> None:
    """Убитый воркер оставляет прогон незавершённым, а тот блокирует запуск.

    Один незавершённый прогон на задачу — условие уникального индекса, и
    повисшая строка иначе навсегда закрыла бы кнопку «Проверить сейчас».
    """
    async with async_session_maker() as session:
        closed = await periodic_domain.close_stale_runs(
            session, older_than=STALE_RUN_AFTER
        )
    if closed:
        log.warning("прогонов закрыто как повисшие: %d", closed)


def start_jobs() -> AsyncIOScheduler:
    """Досрочивание команд и чистка идут всегда.

    RULES_ENABLED выключает только движок правил: неподтверждённая блокировка
    — вопрос безопасности команд, и молчать о ней нельзя даже с выключенными
    правилами.
    """
    scheduler = AsyncIOScheduler()
    if settings.rules_enabled:
        scheduler.add_job(
            run_rules,
            IntervalTrigger(seconds=settings.rules_interval_seconds),
            id="rules",
            replace_existing=True,
        )
    else:
        log.info("Движок правил отключён (RULES_ENABLED=0), досрочивание команд работает")
    scheduler.add_job(
        sweep_commands,
        IntervalTrigger(seconds=COMMAND_SWEEP_SECONDS),
        id="command_timeout",
        replace_existing=True,
    )
    scheduler.add_job(
        cleanup_telemetry,
        IntervalTrigger(hours=CLEANUP_INTERVAL_HOURS),
        id="telemetry_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        cleanup_login_tokens,
        IntervalTrigger(hours=CLEANUP_INTERVAL_HOURS),
        id="login_tokens_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        close_stale_runs,
        IntervalTrigger(minutes=STALE_RUN_SWEEP_MINUTES),
        id="stale_runs",
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "Фоновые задачи: правила %s, досрочивание команд раз в %d с, чистка раз в %d ч",
        f"раз в {settings.rules_interval_seconds} с" if settings.rules_enabled else "выключены",
        COMMAND_SWEEP_SECONDS,
        CLEANUP_INTERVAL_HOURS,
    )
    return scheduler
