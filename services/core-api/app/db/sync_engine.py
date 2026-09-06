"""Синхронный engine для celery: задачи живут вне event loop.

Один экземпляр на процесс — иначе воркер держит несколько независимых пулов
к одной базе, и настройки пришлось бы менять в разных местах.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.config import settings, sync_database_url


@lru_cache(maxsize=1)
def sync_engine() -> Engine:
    return create_engine(sync_database_url(settings.database_url), pool_pre_ping=True)
