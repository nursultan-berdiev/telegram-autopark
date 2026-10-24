"""Мелкие общие хелперы сборки DTO из ORM в роутерах."""
from __future__ import annotations

from datetime import datetime, timezone


def utc(dt: datetime | None) -> datetime | None:
    """SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
