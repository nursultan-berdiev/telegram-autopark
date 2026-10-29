"""Единое место для «момент → часовой пояс парка».

Раньше эта логика дублировалась (schedules._aware, photo_inspections.local_today,
ассистент, бот). В БД/через SQLite дата может прийти naive — считаем её UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings


def aware_utc(dt: datetime) -> datetime:
    """Гарантирует tz-aware datetime: naive трактуем как UTC (в БД хранится UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def to_park_tz(dt: datetime) -> datetime:
    """Момент в часовом поясе парка (`settings.timezone`); naive считаем UTC."""
    return aware_utc(dt).astimezone(ZoneInfo(settings.timezone))
