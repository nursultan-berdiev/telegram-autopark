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

    # --- Вечерние уведомления у срока 22:00 (3 независимо отключаемые задачи) --
    # Каждая задача включается/выключается своим флагом (отключить — флаг в 0 +
    # перезапуск бота). Время — "ЧЧ:ММ" по часовому поясу парка.
    payment_warn_enabled: bool = Field(default=True, alias="PAYMENT_WARN_ENABLED")
    payment_block_notice_enabled: bool = Field(
        default=True, alias="PAYMENT_BLOCK_NOTICE_ENABLED"
    )
    payment_digest_enabled: bool = Field(default=True, alias="PAYMENT_DIGEST_ENABLED")
    payment_warn_at: str = Field(default="21:45", alias="PAYMENT_WARN_AT")
    payment_block_notice_at: str = Field(default="22:00", alias="PAYMENT_BLOCK_NOTICE_AT")
    payment_digest_at: str = Field(default="22:05", alias="PAYMENT_DIGEST_AT")

    @field_validator("payment_warn_at", "payment_block_notice_at", "payment_digest_at")
    @classmethod
    def _valid_hm(cls, value: str) -> str:
        """Формат ЧЧ:ММ. Кривое значение роняет бот на старте — иначе задача
        молча встала бы на полночь и слала бы водителям среди ночи."""
        try:
            hour, minute = (int(part) for part in value.split(":", 1))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"время задачи должно быть ЧЧ:ММ, получено {value!r}") from exc
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f"время задачи вне диапазона ЧЧ:ММ: {value!r}")
        return value

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
