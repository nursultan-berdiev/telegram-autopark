"""Конфигурация приложения из переменных окружения.

Своей БД у адаптера нет — только адреса core-api/Traccar и токены. Паттерн
(pydantic-settings, extra=ignore, singleton settings) — как в services/bot/app/config.py.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    core_api_url: str = Field(alias="CORE_API_URL")
    # Отдельный от ADAPTER_TOKEN — компрометация одного не даёт прав на другое.
    ingest_token: str = Field(alias="INGEST_TOKEN")
    adapter_token: str = Field(alias="ADAPTER_TOKEN")

    traccar_url: str = Field(alias="TRACCAR_URL")
    traccar_user: str = Field(alias="TRACCAR_USER")
    traccar_password: str = Field(alias="TRACCAR_PASSWORD")
    traccar_ws_enabled: bool = Field(default=True, alias="TRACCAR_WS_ENABLED")

    telemetry_batch_size: int = Field(default=50, alias="TELEMETRY_BATCH_SIZE")
    telemetry_flush_seconds: float = Field(default=10.0, alias="TELEMETRY_FLUSH_SECONDS")
    # Фолбэк-поллинг /api/positions, если WS выключен (TRACCAR_WS_ENABLED=0).
    poll_interval_seconds: float = Field(default=30.0, alias="POLL_INTERVAL_SECONDS")


settings = Settings()  # type: ignore[call-arg]
