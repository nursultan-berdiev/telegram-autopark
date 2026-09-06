"""Задача-пробник: проверяет, что цепочка beat → брокер → воркер → БД жива."""
from __future__ import annotations

import logging

from app.db.models import TaskRunStatus
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

NAME = "app.tasks.ping.ping"


@celery_app.task(name=NAME)
def ping(periodic_task_id: int | None = None) -> str:
    with record_run(NAME, periodic_task_id) as run:
        run.status = TaskRunStatus.ok
        run.detail = "планировщик, брокер и воркер на связи"
    log.info("ping выполнен")
    return "pong"
