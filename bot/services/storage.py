"""Сохранение загруженных из Telegram файлов на сервере.

Храним и file_id (для дешёвой повторной отправки), и локальную копию
файла на диске (требование ФТ: данные хранятся на сервере).
"""
from __future__ import annotations

import io
from pathlib import Path

from aiogram import Bot

from bot.config import settings

_EXT_MEDIA_TYPE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


async def download_file_bytes(bot: Bot, file_id: str) -> tuple[bytes, str]:
    """Скачивает файл Telegram в память, возвращает (bytes, media_type)."""
    tg_file = await bot.get_file(file_id)
    src_path = tg_file.file_path or ""
    ext = Path(src_path).suffix.lower()
    media_type = _EXT_MEDIA_TYPE.get(ext, "image/jpeg")
    buf = io.BytesIO()
    await bot.download_file(src_path, destination=buf)
    return buf.getvalue(), media_type


async def save_telegram_file(bot: Bot, file_id: str, subdir: str, name: str) -> str:
    """Скачивает файл Telegram в FILES_DIR/subdir/name.<ext> и возвращает путь.

    Расширение берётся из пути файла на серверах Telegram.
    """
    tg_file = await bot.get_file(file_id)
    src_path = tg_file.file_path or ""
    ext = Path(src_path).suffix or ".jpg"

    dest_dir = settings.files_dir / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}{ext}"

    await bot.download_file(src_path, destination=dest)
    return str(dest)
