"""Платёжные уведомления как периодические задачи celery-beat.

Единый стандарт: расписание — строками `periodic_tasks` (управляется из админки),
а не бот-джобами. celery не имеет доступа в Telegram, поэтому задачи считают через
домен `reminders.*` и кладут готовые сообщения в очередь `outbound_messages`
(`app.domain.outbox`); бот петлёй `drain_outbox` их отправляет. Дедуп «раз в день»
— прежними колонками графика (`last_reminded_on`/`last_warned_on`/`last_block_notice_on`).
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.models import TaskRunStatus
from app.domain import outbox as outbox_domain
from app.domain import reminders as reminders_domain
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

DAILY = "app.tasks.notifications.notify_daily_reminders"
WARN = "app.tasks.notifications.notify_overdue_warning"
BLOCK = "app.tasks.notifications.notify_block_notice"
DIGEST = "app.tasks.notifications.notify_admin_digest"


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


async def _run_daily() -> dict[str, int]:
    now = _now()
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    out: Counter[str] = Counter()
    async with session_scope() as session:
        plan = await reminders_domain.collect(session, now, settings.timezone)
        for r in plan.reminders:
            await outbox_domain.enqueue(
                session, recipient_tg_user_id=r.tg_user_id, text=r.text, kind="reminder"
            )
            out["drivers"] += 1
        digest = plan.owner_digest()
        if digest:
            for admin_id in settings.admin_ids:
                await outbox_domain.enqueue(
                    session, recipient_tg_user_id=admin_id, text=digest, kind="digest"
                )
                out["owners"] += 1
        await reminders_domain.mark_reminded(
            session, [r.schedule_id for r in plan.reminders], today
        )
        # mark_reminded при пустом списке напоминаний делает ранний return без
        # commit — иначе сводка владельцу (digest) откатилась бы при закрытии сессии.
        await session.commit()
    return dict(out)


async def _run_driver_notice(kind: str) -> dict[str, int]:
    now = _now()
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    out: Counter[str] = Counter()
    async with session_scope() as session:
        if kind == "warn":
            rows = await reminders_domain.collect_overdue_warning(
                session, now, settings.timezone, lead_minutes=settings.warn_lead_minutes
            )
        else:
            rows = await reminders_domain.collect_block_notice(session, now, settings.timezone)
        for r in rows:
            await outbox_domain.enqueue(
                session, recipient_tg_user_id=r.tg_user_id, text=r.text, kind=kind
            )
            out["drivers"] += 1
        ids = [r.schedule_id for r in rows]
        if kind == "warn":
            await reminders_domain.mark_warned(session, ids, today)
        else:
            await reminders_domain.mark_block_notice(session, ids, today)
        await session.commit()  # на случай пустого ids (ранний return в mark_*)
    return dict(out)


async def _run_digest() -> dict[str, int]:
    now = _now()
    out: Counter[str] = Counter()
    async with session_scope() as session:
        text = await reminders_domain.collect_admin_digest(session, now, settings.timezone)
        if text:
            for admin_id in settings.admin_ids:
                await outbox_domain.enqueue(
                    session, recipient_tg_user_id=admin_id, text=text, kind="digest"
                )
                out["owners"] += 1
        await session.commit()
    return dict(out)


@celery_app.task(name=DAILY)
def notify_daily_reminders(periodic_task_id: int | None = None, run_id: int | None = None):
    with record_run(DAILY, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(_run_daily)
        run.status = TaskRunStatus.ok
        run.detail = f"водителям {outcomes.get('drivers', 0)}, владельцам {outcomes.get('owners', 0)}"
        run.payload = outcomes
    log.info("notify_daily_reminders: %s", outcomes)
    return outcomes


@celery_app.task(name=WARN)
def notify_overdue_warning(periodic_task_id: int | None = None, run_id: int | None = None):
    with record_run(WARN, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(lambda: _run_driver_notice("warn"))
        run.status = TaskRunStatus.ok
        run.detail = f"предупреждений {outcomes.get('drivers', 0)}"
        run.payload = outcomes
    log.info("notify_overdue_warning: %s", outcomes)
    return outcomes


@celery_app.task(name=BLOCK)
def notify_block_notice(periodic_task_id: int | None = None, run_id: int | None = None):
    with record_run(BLOCK, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(lambda: _run_driver_notice("block_notice"))
        run.status = TaskRunStatus.ok
        run.detail = f"уведомлений о блоке {outcomes.get('drivers', 0)}"
        run.payload = outcomes
    log.info("notify_block_notice: %s", outcomes)
    return outcomes


@celery_app.task(name=DIGEST)
def notify_admin_digest(periodic_task_id: int | None = None, run_id: int | None = None):
    with record_run(DIGEST, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(_run_digest)
        run.status = TaskRunStatus.ok
        run.detail = f"дайджест владельцам {outcomes.get('owners', 0)}"
        run.payload = outcomes
    log.info("notify_admin_digest: %s", outcomes)
    return outcomes
