"""Конфигурация core-api из переменных окружения.

Токены разделены по областям: компрометация адаптера не должна давать
прав на команды блокировки (см. plan/03).
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = Field(alias="DATABASE_URL")
    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list, alias="ADMIN_IDS"
    )

    # --- Токены по областям ---------------------------------------------------
    core_api_token: str = Field(default="", alias="CORE_API_TOKEN")
    ingest_token: str = Field(default="", alias="INGEST_TOKEN")
    # Раннер живёт в браузере владельца — самой ненадёжной поверхности,
    # и мастер-ключ ему давать нельзя.
    fines_import_token: str = Field(default="", alias="FINES_IMPORT_TOKEN")
    adapter_url: str = Field(default="", alias="ADAPTER_URL")
    adapter_token: str = Field(default="", alias="ADAPTER_TOKEN")

    # --- ИИ (переехало из бота) ----------------------------------------------
    ai_backend: str = Field(default="auto", alias="AI_BACKEND")
    ai_timeout_seconds: float = Field(default=120.0, alias="AI_TIMEOUT_SECONDS")
    gateway_url: str = Field(default="", alias="GATEWAY_URL")
    gateway_api_key: str = Field(default="", alias="GATEWAY_API_KEY")
    gateway_model: str = Field(default="sonnet", alias="GATEWAY_MODEL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-opus-4-8", alias="ANTHROPIC_MODEL")
    claude_cli_path: str = Field(default="claude", alias="CLAUDE_CLI_PATH")
    claude_cli_model: str = Field(default="sonnet", alias="CLAUDE_CLI_MODEL")

    files_dir: Path = Field(default=Path("./storage"), alias="FILES_DIR")
    timezone: str = Field(default="Asia/Bishkek", alias="TZ")
    reminder_hour: int = Field(default=9, alias="REMINDER_HOUR")

    # TTL приглашения считает сервер: он же их создаёт и валидирует.
    invite_ttl_hours: int = Field(default=24, alias="INVITE_TTL_HOURS")
    invite_ttl_minutes: int = Field(default=0, alias="INVITE_TTL_MINUTES")

    # --- Правила и тайминги ---------------------------------------------------
    rules_enabled: bool = Field(default=True, alias="RULES_ENABLED")
    rules_interval_seconds: int = Field(default=120, alias="RULES_INTERVAL_SECONDS")
    telemetry_stale_seconds: int = Field(default=300, alias="TELEMETRY_STALE_SECONDS")
    command_ack_window_seconds: int = Field(
        default=180, alias="COMMAND_ACK_WINDOW_SECONDS"
    )
    command_dedup_seconds: int = Field(default=60, alias="COMMAND_DEDUP_SECONDS")
    telemetry_retention_days: int = Field(default=180, alias="TELEMETRY_RETENTION_DAYS")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> list[int]:
        """Разбирает "111,222" из env в список int."""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [int(v) for v in value]
        return [int(value)]  # type: ignore[arg-type]

    @property
    def invite_ttl(self) -> timedelta:
        if self.invite_ttl_minutes > 0:
            return timedelta(minutes=self.invite_ttl_minutes)
        return timedelta(hours=self.invite_ttl_hours)

    @property
    def invite_ttl_label(self) -> str:
        if self.invite_ttl_minutes > 0:
            return f"{self.invite_ttl_minutes} мин"
        return f"{self.invite_ttl_hours} ч"

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_ids


settings = Settings()  # type: ignore[call-arg]
