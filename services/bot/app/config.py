"""Конфигурация приложения из переменных окружения.

Список администраторов задаётся ТОЛЬКО здесь (ADMIN_IDS) и не может быть
изменён через интерфейс бота — это требование FR-ADM-1/2/3.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")
    admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list, alias="ADMIN_IDS"
    )
    core_api_url: str = Field(default="http://core-api:8000", alias="CORE_API_URL")
    core_api_token: str = Field(default="", alias="CORE_API_TOKEN")
    api_timeout_seconds: float = Field(default=30.0, alias="API_TIMEOUT_SECONDS")

    # --- Напоминания о платежах ---------------------------------------------
    # Внутри всё считается в UTC; напоминания шлём по локальному времени парка.
    reminders_enabled: bool = Field(default=True, alias="REMINDERS_ENABLED")
    timezone: str = Field(default="Asia/Bishkek", alias="TZ")
    reminder_hour: int = Field(default=9, alias="REMINDER_HOUR")

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

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


settings = Settings()  # type: ignore[call-arg]
