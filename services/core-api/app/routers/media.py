"""Прокси к файлам Telegram для веб-админки.

Фото машин, селфи водителей, документы и фотоконтроль хранятся как Telegram
file_id — байтов тут нет. Резолвим file_id из БД по виду и id, затем берём
байты у медиа-сервера бота. Доступ — чтение под require_core: админ-кука
веб-админки (для тега <img> без CSRF-заголовка) либо сервисный токен.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.clients.bot_media import BotMediaError, fetch_media
from app.db.models import Car, Document, Driver, PhotoInspection
from app.db.session import get_session

log = logging.getLogger(__name__)
router = APIRouter()

# Картинки отдаём inline (нужно тегу <img>); всё остальное — только как
# вложение. Тип вне белого списка приводим к octet-stream: файлы приходят из
# Telegram с исходным расширением, и text/html или image/svg+xml с нашего
# origin (где кука админа) были бы хранимым XSS.
_INLINE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif"})
_SAFE_TYPES = _INLINE_TYPES | {"application/pdf"}

# Ракурсы фотоконтроля → колонка с file_id. Бока добавлены в 0027 и у старых
# осмотров пусты — тогда вернётся 404, как и для отсутствующего файла.
_INSPECTION_ANGLES = {
    "front": PhotoInspection.front_file_id,
    "rear": PhotoInspection.rear_file_id,
    "left": PhotoInspection.left_file_id,
    "right": PhotoInspection.right_file_id,
    "interior_front": PhotoInspection.interior_front_file_id,
    "interior_rear": PhotoInspection.interior_rear_file_id,
}


async def _resolve_file_id(
    session: AsyncSession, kind: str, ref_id: int, angle: str | None
) -> str | None:
    if kind == "car-photo":
        return await session.scalar(select(Car.photo_file_id).where(Car.id == ref_id))
    if kind == "driver-selfie":
        return await session.scalar(select(Driver.selfie_file_id).where(Driver.id == ref_id))
    if kind == "document":
        return await session.scalar(select(Document.file_id).where(Document.id == ref_id))
    if kind == "inspection":
        column = _INSPECTION_ANGLES.get(angle or "")
        if column is None:
            raise HTTPException(status_code=400, detail="неизвестный ракурс фотоконтроля")
        return await session.scalar(select(column).where(PhotoInspection.id == ref_id))
    raise HTTPException(status_code=404, detail="неизвестный вид файла")


@router.get("/media/{kind}/{ref_id}", dependencies=[Depends(require_core)])
async def get_media(
    kind: str,
    ref_id: int,
    angle: Annotated[str | None, Query()] = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    file_id = await _resolve_file_id(session, kind, ref_id, angle)
    if not file_id:
        raise HTTPException(status_code=404, detail="файл не найден")
    try:
        data, content_type = await fetch_media(file_id)
    except BotMediaError as exc:
        # Текст исключения может содержать внутренний адрес (bot:8081) — в лог, не клиенту.
        log.warning("media %s/%s: %s", kind, ref_id, exc)
        raise HTTPException(status_code=502, detail="файл временно недоступен") from exc

    # Доверять типу от бота нельзя (защита в два слоя): только белый список,
    # иначе octet-stream. Плюс nosniff, вложение для не-картинок и жёсткий CSP —
    # чтобы прямое открытие ссылки не исполнило ничего в нашем origin.
    base_type = content_type.split(";", 1)[0].strip().lower()
    safe_type = base_type if base_type in _SAFE_TYPES else "application/octet-stream"
    disposition = "inline" if safe_type in _INLINE_TYPES else "attachment"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": disposition,
        "Content-Security-Policy": "default-src 'none'; sandbox",
        # Содержимое по file_id неизменно; приватное — не кэшировать на общих прокси.
        "Cache-Control": "private, max-age=3600",
        "Vary": "Cookie",
    }
    return Response(content=data, media_type=safe_type, headers=headers)
