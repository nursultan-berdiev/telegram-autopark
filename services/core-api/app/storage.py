"""Файловое хранилище чеков и селфи на стороне core-api.

Telegram-специфика осталась в боте: сюда приходят уже скачанные байты,
поэтому здесь нет ни aiogram, ни file_id.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings

_MEDIA_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
}

_EXT_MEDIA_TYPE = {ext: mt for mt, ext in _MEDIA_TYPE_EXT.items()} | {".jpeg": "image/jpeg"}


def ext_for(media_type: str | None) -> str:
    return _MEDIA_TYPE_EXT.get((media_type or "").lower(), ".jpg")


def media_type_for(filename: str | None) -> str:
    return _EXT_MEDIA_TYPE.get(Path(filename or "").suffix.lower(), "image/jpeg")


def save_bytes(data: bytes, subdir: str, name: str, media_type: str | None = None) -> str:
    """Кладёт байты в FILES_DIR/subdir/name.<ext>, возвращает путь."""
    dest_dir = settings.files_dir / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}{ext_for(media_type)}"
    dest.write_bytes(data)
    return str(dest)
