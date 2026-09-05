"""Реестр провайдеров трекеров. v1: только "traccar" — расширяется по мере подключения новых вендоров."""
from __future__ import annotations

from app.config import settings
from app.providers.base import TrackerProvider
from app.providers.traccar import TraccarProvider

_providers: dict[str, TrackerProvider] = {}


def get_provider(name: str) -> TrackerProvider:
    if name not in _providers:
        if name != "traccar":
            raise ValueError(f"unknown tracker provider: {name!r}")
        _providers[name] = TraccarProvider(
            settings.traccar_url,
            settings.traccar_user,
            settings.traccar_password,
            ws_enabled=settings.traccar_ws_enabled,
            poll_interval_seconds=settings.poll_interval_seconds,
        )
    return _providers[name]
