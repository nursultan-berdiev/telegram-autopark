"""Внутренний HTTP-сервер бота: байты файла по Telegram file_id.

Фото машин, селфи водителей, документы и фотоконтроль хранятся как Telegram
file_id — байтов нет ни в core-api, ни на диске. Скачать их может только тот,
у кого токен бота, поэтому веб-админка (core-api) ходит за ними сюда. Порт
наружу не публикуется; доступ закрыт общим CORE_API_TOKEN (тем же, что бот шлёт
в core-api), чтобы в одной docker-сети к файлам не дотянулся посторонний.
"""
from __future__ import annotations

import hmac
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiohttp import web

from app.config import settings

logger = logging.getLogger(__name__)

# Типизированный ключ вместо строки: aiohttp 3.9+ иначе шлёт NotAppKeyWarning.
_BOT_KEY: web.AppKey[Bot] = web.AppKey("bot", Bot)

# Тип определяем ТОЛЬКО по сигнатуре содержимого, а не по пути файла в Telegram:
# у документов getFile сохраняет исходное расширение, и mimetypes отдал бы
# text/html / image/svg+xml — с origin core-api (где кука админа) это хранимый
# XSS. Неопознанное → octet-stream. core-api дополнительно фильтрует по белому
# списку (защита в два слоя).
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF", "application/pdf"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

_NOSNIFF = {"X-Content-Type-Options": "nosniff"}


def _content_type(data: bytes) -> str:
    for magic, ctype in _MAGIC:
        if data.startswith(magic):
            return ctype
    return "application/octet-stream"


async def _media(request: web.Request) -> web.Response:
    expected = settings.core_api_token
    if not expected:
        # Без общего токена эндпойнт открыт всем в сети — безопаснее не отдавать.
        raise web.HTTPServiceUnavailable(reason="CORE_API_TOKEN не задан")
    provided = request.headers.get("Authorization", "")
    if not hmac.compare_digest(provided, f"Bearer {expected}"):
        raise web.HTTPUnauthorized()

    file_id = request.match_info["file_id"]
    bot = request.app[_BOT_KEY]
    try:
        tg_file = await bot.get_file(file_id)
        if not tg_file.file_path:
            raise web.HTTPNotFound()
        buffer = await bot.download_file(tg_file.file_path)
    except (TelegramNotFound, TelegramBadRequest, TelegramEntityTooLarge) as exc:
        # нет файла / неверный file_id / больше 20 МБ — для админки это «нет файла»
        logger.info("медиа %s недоступен: %s", file_id, exc)
        raise web.HTTPNotFound() from exc
    except (TelegramRetryAfter, TelegramNetworkError, TelegramServerError) as exc:
        # временный сбой Telegram — пусть клиент повторит, а не считает файл утраченным
        logger.warning("медиа %s: временный сбой Telegram: %s", file_id, exc)
        raise web.HTTPBadGateway() from exc
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("медиа %s: неожиданная ошибка", file_id)
        raise web.HTTPInternalServerError() from None

    data = buffer.read()
    return web.Response(body=data, content_type=_content_type(data), headers=_NOSNIFF)


def build_media_app(bot: Bot) -> web.Application:
    app = web.Application()
    app[_BOT_KEY] = bot
    app.router.add_get("/media/{file_id}", _media)
    return app


async def start_media_server(bot: Bot) -> web.AppRunner:
    """Поднимает медиа-сервер рядом с long-polling. Возвращает runner для
    остановки в finally. При занятом порте чистит runner и пробрасывает OSError —
    решение, падать или продолжать без медиа, принимает вызывающий."""
    runner = web.AppRunner(build_media_app(bot))
    await runner.setup()
    site = web.TCPSite(runner, host=settings.media_host, port=settings.media_port)
    try:
        await site.start()
    except OSError:
        await runner.cleanup()
        raise
    logger.info("Медиа-сервер бота слушает %s:%d", settings.media_host, settings.media_port)
    return runner
