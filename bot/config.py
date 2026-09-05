"""Конфигурация приложения из переменных окружения.

Список администраторов задаётся ТОЛЬКО здесь (ADMIN_IDS) и не может быть
изменён через интерфейс бота — это требование FR-ADM-1/2/3.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
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
    database_url: str = Field(alias="DATABASE_URL")

    # --- ИИ (распознавание чеков + аналитика) --------------------------------
    # Backend выбора модели Claude:
    #   "http" — через claude-gateway по HTTP (подписка живёт в шлюзе;
    #            боту не нужен ни ключ, ни claude в образе — годится для docker);
    #   "api"  — прямой Anthropic API (нужен ANTHROPIC_API_KEY);
    #   "cli"  — Claude Code `claude -p` на ПОДПИСКЕ локально (без ключа);
    #   "auto" — http при заданном GATEWAY_URL, иначе api при ключе, иначе cli.
    ai_backend: str = Field(default="auto", alias="AI_BACKEND")
    ai_timeout_seconds: float = Field(default=120.0, alias="AI_TIMEOUT_SECONDS")
    # Путь через шлюз (claude-gateway).
    gateway_url: str = Field(default="", alias="GATEWAY_URL")
    gateway_api_key: str = Field(default="", alias="GATEWAY_API_KEY")
    gateway_model: str = Field(default="sonnet", alias="GATEWAY_MODEL")
    # Путь через API-ключ.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-opus-4-8", alias="ANTHROPIC_MODEL")
    # Путь через подписку (Claude Code CLI).
    claude_cli_path: str = Field(default="claude", alias="CLAUDE_CLI_PATH")
    claude_cli_model: str = Field(default="sonnet", alias="CLAUDE_CLI_MODEL")

    files_dir: Path = Field(default=Path("./storage"), alias="FILES_DIR")
    invite_ttl_hours: int = Field(default=24, alias="INVITE_TTL_HOURS")
    # Для QA-стенда: если > 0, перекрывает часы. Позволяет проверить сценарий
    # «ссылка истекла» за пару минут, а не ждать сутки. В бою — 0.
    invite_ttl_minutes: int = Field(default=0, alias="INVITE_TTL_MINUTES")

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

    @property
    def invite_ttl(self) -> timedelta:
        """Срок жизни приглашения (минуты перекрывают часы — см. INVITE_TTL_MINUTES)."""
        if self.invite_ttl_minutes > 0:
            return timedelta(minutes=self.invite_ttl_minutes)
        return timedelta(hours=self.invite_ttl_hours)

    @property
    def invite_ttl_label(self) -> str:
        """Человекочитаемый срок — показываем админу в тексте приглашения."""
        if self.invite_ttl_minutes > 0:
            return f"{self.invite_ttl_minutes} мин"
        return f"{self.invite_ttl_hours} ч"

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


settings = Settings()  # type: ignore[call-arg]
