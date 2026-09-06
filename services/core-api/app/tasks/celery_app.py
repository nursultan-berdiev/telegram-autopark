"""Celery-приложение core-api.

Задачи живут рядом с доменом и работают с той же схемой: отдельный сервис
со своей копией моделей разошёлся бы с core-api на первой же миграции.
"""
from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "autopark",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.ping", "app.tasks.fines"],
)

celery_app.conf.update(
    timezone=settings.timezone,
    enable_utc=True,
    task_track_started=True,
    # Проверка парка занимает минуты: без запаса брокер вернёт задачу в
    # очередь на середине прогона и номера пойдут по второму кругу.
    task_time_limit=60 * 30,
    task_soft_time_limit=60 * 25,
    worker_max_tasks_per_child=50,
    beat_scheduler="app.tasks.beat:DatabaseScheduler",
)
