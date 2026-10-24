"""HTTP-клиент к медиа-серверу бота: Telegram file_id → байты.

Байтов файлов у core-api нет (фото, селфи, документы, фотоконтроль живут в
Telegram), скачать умеет только бот. Вынесено в отдельный модуль ради
мокаемости в тестах роутера media.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class BotMediaError(Exception):
    """Файл недоступен: нет файла в Telegram или бот недоступен."""


async def fetch_media(file_id: str) -> tuple[bytes, str]:
    """Возвращает (байты, content-type); любую неудачу — через BotMediaError."""
    url = f"{settings.bot_media_url.rstrip('/')}/media/{quote(file_id, safe='')}"
    headers = {"Authorization": f"Bearer {settings.core_api_token}"}
    try:
        async with httpx.AsyncClient(timeout=settings.bot_media_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise BotMediaError(f"медиа-сервер бота недоступен: {exc}") from exc
    if response.status_code == 404:
        raise BotMediaError("файл не найден в Telegram")
    if response.status_code >= 400:
        raise BotMediaError(f"медиа-сервер бота вернул {response.status_code}")
    content_type = response.headers.get("content-type", "application/octet-stream")
    return response.content, content_type
