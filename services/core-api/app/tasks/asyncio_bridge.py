"""Мост между синхронным celery и асинхронным доменом.

Задача исполняется вне event loop, а внутри чередует асинхронные шаги с
синхронным браузером — значит `asyncio.run` вызывается несколько раз за
прогон. Общий пул asyncpg этого не переживает: соединения принадлежат тому
loop, в котором открыты, и во втором вызове падают с «attached to a
different loop». Поэтому у задач своя фабрика сессий без пула.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

T = TypeVar("T")


@lru_cache(maxsize=1)
def task_session_maker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def run_async(factory: Callable[[], Awaitable[T]]) -> T:
    """Выполняет корутину в собственном loop и закрывает его за собой."""
    return asyncio.run(factory())


def session_scope() -> AsyncSession:
    """Сессия для задачи; использовать через `async with`.

    AsyncSession сам по себе асинхронный контекстный менеджер, поэтому
    отдельной обёртки не нужно — но по сигнатуре это не очевидно.
    """
    return task_session_maker()()
