"""Документы машин и общие документы парка.

Сам файл хранит Telegram (бот пересылает его по file_id), здесь — только учёт:
к какой машине относится, как называется, кто загрузил.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Car, Document
from app.errors import NotFound, Validation
from contracts import DOCUMENT_MAX_TITLE


async def add_document(
    session: AsyncSession,
    *,
    car_id: int | None,
    title: str,
    file_id: str,
    file_kind: str,
    file_name: str | None,
    file_size: int | None,
    created_by: int,
) -> Document:
    """Заводит документ. Поля файла уже проверены контрактом DocumentCreate."""
    name = title.strip()
    if not name:
        raise Validation("пустое название документа")
    if len(name) > DOCUMENT_MAX_TITLE:
        raise Validation(f"название длиннее {DOCUMENT_MAX_TITLE} символов")
    if car_id is not None and await session.get(Car, car_id) is None:
        raise NotFound(f"машина {car_id} не найдена")

    document = Document(
        car_id=car_id,
        title=name,
        file_id=file_id,
        file_kind=file_kind,
        file_name=file_name,
        file_size=file_size,
        created_by=created_by,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


async def list_documents(session: AsyncSession, *, car_id: int | None) -> list[Document]:
    """Документы машины, а при car_id=None — общие документы парка. Свежие первыми:
    новый полис встаёт над старым, пока тот не удалили."""
    owner = Document.car_id.is_(None) if car_id is None else Document.car_id == car_id
    result = await session.scalars(
        select(Document)
        .where(owner)
        .order_by(Document.created_at.desc(), Document.id.desc())
    )
    return list(result.all())


async def get_document(session: AsyncSession, document_id: int) -> Document | None:
    return await session.get(Document, document_id)


async def delete_document(session: AsyncSession, document_id: int) -> bool:
    """True — документ был и удалён (иначе роутер отдаст 404)."""
    document = await session.get(Document, document_id)
    if document is None:
        return False
    await session.delete(document)
    await session.commit()
    return True
