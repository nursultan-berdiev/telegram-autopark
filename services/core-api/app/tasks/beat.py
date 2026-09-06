"""Планировщик celery, читающий расписание из таблицы periodic_tasks.

Аналог DatabaseScheduler из django-celery-beat: расписание правится в
админке и подхватывается на лету, без перезапуска beat.

В проекте два планировщика, и это разделение намеренное:
  * APScheduler в app/jobs.py — внутренние задачи с фиксированным ритмом
    (досрочивание команд, движок правил). Их период менять некому;
  * Celery Beat отсюда — задачи, у которых расписание настраивают люди
    (проверка штрафов). Новую задачу добавляйте туда, где ей место по
    этому признаку.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from celery.beat import ScheduleEntry, Scheduler
from celery.schedules import schedule
from celery.schedules import crontab as celery_crontab
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PeriodicTask
from app.db.sync_engine import sync_engine

log = logging.getLogger(__name__)

# Как часто beat перечитывает таблицу. Меньше — лишняя нагрузка на БД,
# больше — правка расписания в админке долго не вступает в силу.
SYNC_EVERY_SECONDS = 30


def _aware(value: datetime | None) -> datetime | None:
    """SQLite отдаёт naive datetime, а celery сравнивает с aware."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_crontab(spec: str) -> celery_crontab:
    """«m h dom mon dow» из таблицы в расписание celery."""
    minute, hour, day_of_month, month_of_year, day_of_week = spec.split()
    return celery_crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )


def entry_schedule(row: PeriodicTask) -> schedule | celery_crontab:
    if row.interval_seconds is not None:
        return schedule(run_every=row.interval_seconds)
    return parse_crontab(row.crontab or "")


class DatabaseScheduler(Scheduler):
    """Держит расписание в БД, а не в конфиге celery."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._engine = sync_engine()
        # Имя намеренно не пересекается с внутренними атрибутами Scheduler:
        # базовый класс держит в `_last_sync` монотонное время и сравнивает
        # его с time.monotonic(), а datetime там роняет beat на первом тике.
        self._db_synced_at: datetime | None = None
        super().__init__(*args, **kwargs)

    # --- чтение расписания ---------------------------------------------------

    def _rows(self) -> list[PeriodicTask]:
        with Session(self._engine) as session:
            return list(
                session.scalars(
                    select(PeriodicTask).where(PeriodicTask.enabled.is_(True))
                ).all()
            )

    def _build(self) -> dict[str, ScheduleEntry]:
        entries: dict[str, ScheduleEntry] = {}
        for row in self._rows():
            try:
                entries[row.name] = ScheduleEntry(
                    name=row.name,
                    task=row.task,
                    schedule=entry_schedule(row),
                    # Без времени последнего запуска ScheduleEntry считает,
                    # что задачу «только что выполнили». Таблица перечитывается
                    # чаще минимального интервала, поэтому таймер обнулялся бы
                    # на каждой пересборке и задача не запустилась бы никогда.
                    last_run_at=_aware(row.last_run_at),
                    total_run_count=row.total_run_count or 0,
                    kwargs=dict(row.args or {}, periodic_task_id=row.id),
                    app=self.app,
                )
            except Exception as exc:
                # Одно битое расписание не должно ронять весь beat: остальные
                # задачи обязаны продолжать ходить.
                log.error("расписание %r пропущено: %s", row.name, exc)
        return entries

    def setup_schedule(self) -> None:
        # base Scheduler держит расписание в self.data — заполняем его же,
        # иначе служебные методы работают с пустым словарём.
        self.data = self._build()
        self._db_synced_at = datetime.now(timezone.utc)
        log.info("расписаний загружено: %d", len(self.data))

    @property
    def schedule(self) -> dict[str, ScheduleEntry]:
        now = datetime.now(timezone.utc)
        if (
            self._db_synced_at is None
            or (now - self._db_synced_at).total_seconds() >= SYNC_EVERY_SECONDS
        ):
            self.data = self._build()
            self._db_synced_at = now
        return self.data

    # --- отметка о запуске ---------------------------------------------------

    def apply_entry(self, entry: ScheduleEntry, producer: Any = None) -> None:
        super().apply_entry(entry, producer=producer)
        task_id = (entry.kwargs or {}).get("periodic_task_id")
        if task_id is None:
            return
        with Session(self._engine) as session:
            row = session.get(PeriodicTask, task_id)
            if row is None:
                return
            row.last_run_at = datetime.now(timezone.utc)
            row.total_run_count = (row.total_run_count or 0) + 1
            session.commit()


__all__ = ["DatabaseScheduler", "parse_crontab"]
