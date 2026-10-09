"""Авто-блокировка/разблокировка двигателя по числу неоплаченных штрафов.

Одна периодическая задача (блок и разблок в одном проходе — у штрафов, в отличие
от аренды, нет привязки к времени суток): машинам с неоплаченных штрафов СТРОГО
больше порога взводит блокировку, а тем, кто погасил до ≤ порога — снимает.
Политика и механика — в `app.domain.engine_enforcement`; здесь только обёртка.

Порог N: из `args.threshold` строки periodic_tasks (редактируется в рантайме),
иначе из `settings.fines_block_threshold`. Источник команд — `CommandSource.fines`,
поэтому с блокировкой за аренду и ручной блокировкой не пересекается.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from app.config import settings
from app.db.models import CommandSource, TaskRunStatus
from app.domain import engine_enforcement as enforcement
from app.domain import fines as fines_domain
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

NAME = "app.tasks.fines_enforcement.enforce_fines"


async def _run(threshold: int) -> dict[str, int]:
    """Взводит блок машинам с >N неоплаченных штрафов и снимает у погасивших.

    commit и изоляция ошибок — по машине (как в payments_block): падение на одной
    не должно оставить без обработки остальных и путаться с «никого». Возвращает
    Counter исходов с префиксами block-/release-.
    """
    now = datetime.now(timezone.utc)
    policy = enforcement.fines_policy(threshold)
    outcomes: Counter[str] = Counter()
    async with session_scope() as session:
        for car_id in await fines_domain.car_ids_over_unpaid(session, threshold):
            try:
                outcome = await enforcement.enforce_block(
                    session, car_id=car_id, policy=policy, now=now
                )
                await session.commit()
                outcomes[f"block-{outcome.value}"] += 1
            except Exception:  # noqa: BLE001 — одна машина не роняет остальных
                await session.rollback()
                log.exception("enforce_fines: сбой блокировки машины %s", car_id)
                outcomes["errors"] += 1
        for car_id in await enforcement.cars_under_system_block(session, CommandSource.fines):
            try:
                outcome = await enforcement.release_if_cleared(
                    session, car_id=car_id, policy=policy, now=now
                )
                await session.commit()
                outcomes[f"release-{outcome.value}"] += 1
            except Exception:  # noqa: BLE001
                await session.rollback()
                log.exception("enforce_fines: сбой разблокировки машины %s", car_id)
                outcomes["errors"] += 1
    return dict(outcomes)


@celery_app.task(name=NAME)
def enforce_fines(
    periodic_task_id: int | None = None,
    run_id: int | None = None,
    threshold: int | None = None,
) -> dict[str, int]:
    """Celery-задача: блокировка/разблокировка по числу неоплаченных штрафов.

    Побочные эффекты: взводит/снимает системные блоки `source=fines`, поднимает
    fines_block_fired/fines_unblock(_failed), пишет прогон в task_runs. Возвращает
    Counter исходов. `threshold` приходит из args строки periodic_tasks.
    """
    n = threshold if threshold is not None else settings.fines_block_threshold
    with record_run(NAME, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(lambda: _run(n))
        run.status = TaskRunStatus.ok
        run.detail = (
            f"порог {n}, взведено {outcomes.get('block-armed', 0)}, "
            f"разблокировано {outcomes.get('release-unblocked', 0)}"
        )
        run.payload = outcomes
    log.info("enforce_fines(threshold=%s): %s", n, outcomes)
    return outcomes
