"""Ежедневные напоминания о фотоконтроле и алерт админу о просрочке (celery-beat).

Расписание — строкой periodic_tasks (заводит миграция 0026, правится в админке).
Расчёт — в `app.domain.photo_inspections.run_daily`; сообщения водителям уходят
через очередь outbound_messages (её шлёт бот), алерт админу — обычным алертом.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db.models import TaskRunStatus
from app.domain import photo_inspections as photo_domain
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

NAME = "app.tasks.photo_inspections.notify_photo_inspections"


async def _run() -> dict[str, int]:
    """Один проход run_daily и коммит; счётчики исходов — для журнала прогонов."""
    async with session_scope() as session:
        outcomes = await photo_domain.run_daily(session, datetime.now(timezone.utc))
        await session.commit()
    return dict(outcomes)


@celery_app.task(name=NAME)
def notify_photo_inspections(
    periodic_task_id: int | None = None, run_id: int | None = None
) -> dict[str, int]:
    """Celery-задача: напоминания водителям и алерт админу; пишет прогон в task_runs."""
    with record_run(NAME, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(_run)
        run.status = TaskRunStatus.ok
        run.detail = (
            f"напоминаний {outcomes.get('reminders', 0)}, "
            f"просрочено {outcomes.get('overdue', 0)}"
        )
        run.payload = outcomes
    log.info("notify_photo_inspections: %s", outcomes)
    return outcomes
