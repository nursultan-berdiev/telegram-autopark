"""Медиа-сервер бота: авторизация, тип по сигнатуре, карта ошибок Telegram."""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramNotFound,
)
from aiohttp.test_utils import TestClient, TestServer

from app.config import settings
from app.media import build_media_app

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00data"


def _bot(file_path: str = "photos/file_0", data: bytes = JPEG) -> MagicMock:
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path=file_path))
    bot.download_file = AsyncMock(return_value=io.BytesIO(data))
    return bot


async def _get(bot, token="secret", header="Bearer secret"):
    headers = {"Authorization": header} if header is not None else {}
    async with TestClient(TestServer(build_media_app(bot))) as client:
        return await client.get("/media/FID", headers=headers)


async def test_without_bearer_401(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    resp = await _get(_bot(), header=None)
    assert resp.status == 401


async def test_wrong_token_401(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    resp = await _get(_bot(), header="Bearer nope")
    assert resp.status == 401


async def test_disabled_when_no_token_503(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "")
    resp = await _get(_bot(), header="Bearer ")
    assert resp.status == 503


async def test_returns_bytes_type_and_nosniff(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    bot = _bot(data=JPEG)
    async with TestClient(TestServer(build_media_app(bot))) as client:
        resp = await client.get("/media/FID", headers={"Authorization": "Bearer secret"})
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "image/jpeg"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert await resp.read() == JPEG
    bot.get_file.assert_awaited_once_with("FID")


async def test_sniffs_pdf(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    resp = await _get(_bot(data=b"%PDF-1.7 ..."))
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "application/pdf"


async def test_html_content_served_as_octet_stream(monkeypatch):
    """Расширение из Telegram не влияет: HTML-байты уходят как octet-stream."""
    monkeypatch.setattr(settings, "core_api_token", "secret")
    resp = await _get(_bot(file_path="docs/x.html", data=b"<html><script>evil</script>"))
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "application/octet-stream"


async def test_empty_file_path_404(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    resp = await _get(_bot(file_path=None))
    assert resp.status == 404


async def test_telegram_not_found_404(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    bot = _bot()
    bot.get_file = AsyncMock(side_effect=TelegramNotFound(method=None, message="no file"))
    assert (await _get(bot)).status == 404


async def test_telegram_bad_request_404(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    bot = _bot()
    bot.get_file = AsyncMock(side_effect=TelegramBadRequest(method=None, message="file is too big"))
    assert (await _get(bot)).status == 404


async def test_telegram_network_error_502(monkeypatch):
    """Временный сбой Telegram — не выдаём за «файла нет»."""
    monkeypatch.setattr(settings, "core_api_token", "secret")
    bot = _bot()
    bot.get_file = AsyncMock(side_effect=TelegramNetworkError(method=None, message="timeout"))
    assert (await _get(bot)).status == 502


async def test_unexpected_error_500(monkeypatch):
    monkeypatch.setattr(settings, "core_api_token", "secret")
    bot = _bot()
    bot.get_file = AsyncMock(side_effect=RuntimeError("boom"))
    assert (await _get(bot)).status == 500
