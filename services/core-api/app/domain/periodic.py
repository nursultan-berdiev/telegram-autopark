"""Расписания фоновых задач и журнал их прогонов.

Планировщик читает расписание из таблицы, а не из кода: частота проверки
штрафов меняется из админки без передеплоя (аналог django-celery-beat).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PeriodicTask, TaskRun, TaskRunStatus
from app.errors import Conflict, Validation

MIN_INTERVAL_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_period(interval_seconds: int | None, crontab: str | None) -> None:
    """Ровно один способ задать период: иначе непонятно, какой из них главный."""
    if (interval_seconds is None) == (crontab is None):
        raise Validation("задайте либо интервал в секундах, либо crontab, но не оба")
    if interval_seconds is not None and interval_seconds < MIN_INTERVAL_SECONDS:
        raise Validation(f"интервал меньше {MIN_INTERVAL_SECONDS} с не имеет смысла")
    if crontab is not None and not croniter.is_valid(crontab):
        raise Validation(f"строка crontab не разобрана: {crontab!r}")


async def list_tasks(session: AsyncSession) -> list[PeriodicTask]:
    result = await session.scalars(select(PeriodicTask).order_by(PeriodicTask.id))
    return list(result.all())


async def get_task(session: AsyncSession, task_id: int) -> PeriodicTask | None:
    return await session.get(PeriodicTask, task_id)


async def create_task(
    session: AsyncSession,
    *,
    name: str,
    task: str,
    interval_seconds: int | None = None,
    crontab: str | None = None,
    args: dict | None = None,
    enabled: bool = True,
) -> PeriodicTask:
    validate_period(interval_seconds, crontab)
    row = PeriodicTask(
        name=name,
        task=task,
        interval_seconds=interval_seconds,
        crontab=crontab,
        args=args,
        enabled=enabled,
    )
    session.add(row)
    # Уникальность имени проверяет БД, а не SELECT перед вставкой: два
    # параллельных запроса прошли бы проверку оба, и второй отдал бы 500.
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict(f"расписание с именем {name!r} уже есть") from None
    await session.refresh(row)
    return row


_UNSET: Any = object()


async def update_task(
    session: AsyncSession,
    task_id: int,
    *,
    name: str = _UNSET,
    task: str = _UNSET,
    interval_seconds: int | None = _UNSET,
    crontab: str | None = _UNSET,
    args: dict | None = _UNSET,
    enabled: bool = _UNSET,
) -> PeriodicTask | None:
    """Частичное обновление: незаданные поля не трогаются.

    Поля перечислены явно, а не приняты как **changes: опечатка в имени
    иначе молча ничего бы не изменила.
    """
    row = await get_task(session, task_id)
    if row is None:
        return None
    for field, value in (
        ("name", name),
        ("task", task),
        ("interval_seconds", interval_seconds),
        ("crontab", crontab),
        ("args", args),
        ("enabled", enabled),
    ):
        if value is not _UNSET:
            setattr(row, field, value)
    validate_period(row.interval_seconds, row.crontab)
    # Имя запоминаем до коммита: после отката объект просрочен, и чтение
    # атрибута полезло бы в БД из обработчика ошибки.
    attempted_name = row.name
    # updated_at обновляется через onupdate — по нему beat понимает,
    # что расписание поменялось, и перечитывает таблицу.
    try:
        await session.commit()
    except IntegrityError:
        # Переименование на занятое имя — тоже ответ пользователю, а не 500.
        await session.rollback()
        raise Conflict(f"расписание с именем {attempted_name!r} уже есть") from None
    await session.refresh(row)
    return row


async def delete_task(session: AsyncSession, task_id: int) -> bool:
    row = await get_task(session, task_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def list_runs(
    session: AsyncSession, *, task: str | None = None, limit: int = 50
) -> list[TaskRun]:
    stmt = select(TaskRun).order_by(TaskRun.started_at.desc()).limit(limit)
    if task is not None:
        stmt = stmt.where(TaskRun.task == task)
    result = await session.scalars(stmt)
    return list(result.all())


async def last_run(session: AsyncSession, task: str) -> TaskRun | None:
    return await session.scalar(
        select(TaskRun).where(TaskRun.task == task).order_by(TaskRun.started_at.desc()).limit(1)
    )


async def consecutive_failures(
    session: AsyncSession, task: str, *, limit: int = 10
) -> int:
    """Сколько последних прогонов подряд закончились не успехом.

    Отказ сервиса не виден снаружи никак: задача отработала, ошибок нет,
    штрафов «нет». Считать серию — единственный способ заметить, что нас
    перестали пускать.
    """
    runs = await list_runs(session, task=task, limit=limit)
    count = 0
    for run in runs:
        if run.finished_at is None:
            # Прогон ещё идёт: он заводится со статусом «неуспех», чтобы
            # молчаливо упавший воркер не выглядел успешным, — но считать
            # его отказом до завершения нельзя.
            continue
        if run.status is TaskRunStatus.ok:
            break
        count += 1
    return count


async def start_run(
    session: AsyncSession, *, task: str, requested_by: int | None = None
) -> tuple[TaskRun, bool]:
    """Заводит строку прогона до постановки задачи в очередь.

    Иначе свой прогон не отличить от кронового, начавшегося в ту же секунду:
    строку создаёт воркер, и id при постановке отдать нечем.

    Статус — «неуспех», как и у `record_run`: незавершённый прогон не должен
    выглядеть успешным. Признак «ещё идёт» — пустой `finished_at`.

    Возвращает прогон и признак «завели новый»: если прогон уже идёт, отдаётся
    он, и ставить задачу в очередь второй раз не нужно.
    """
    run = TaskRun(
        task=task,
        status=TaskRunStatus.failed,
        started_at=datetime.now(timezone.utc),
        detail="поставлена в очередь",
        requested_by=requested_by,
    )
    try:
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError:
        # Прогон уже идёт: гонку отсекает частичный уникальный индекс, а не
        # проверка перед вставкой — два одновременных нажатия иначе оба
        # увидели бы «не идёт» и поставили в очередь два обхода парка.
        existing = await running_task(session, task)
        if existing is None:
            raise
        return existing, False
    await session.commit()
    await session.refresh(run)
    return run, True


async def running_task(session: AsyncSession, task: str) -> TaskRun | None:
    """Незавершённый прогон этой задачи, если он есть."""
    return await session.scalar(
        select(TaskRun)
        .where(TaskRun.task == task, TaskRun.finished_at.is_(None))
        .order_by(TaskRun.started_at.desc())
        .limit(1)
    )


async def get_run(session: AsyncSession, run_id: int) -> TaskRun | None:
    return await session.get(TaskRun, run_id)


async def close_stale_runs(session: AsyncSession, *, older_than: timedelta) -> int:
    """Закрывает прогоны, повисшие незавершёнными.

    Строку прогона закрывает исполнитель, но убитый воркер этого не сделает —
    и тогда незавершённая строка навсегда заблокирует запуск задачи, потому
    что уникальный индекс не даст завести новую.
    """
    deadline = datetime.now(timezone.utc) - older_than
    stale = await session.scalars(
        select(TaskRun).where(
            TaskRun.finished_at.is_(None), TaskRun.started_at < deadline
        )
    )
    closed = 0
    for run in stale:
        run.finished_at = datetime.now(timezone.utc)
        run.detail = (run.detail or "") + " (прогон не завершился)"
        closed += 1
    if closed:
        await session.commit()
    return closed
