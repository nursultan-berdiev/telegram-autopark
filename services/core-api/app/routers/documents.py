"""Роутер documents: документы машин и общие документы парка.

Загружает и удаляет только админ (проверка по X-TG-User-Id на сервере). Читает
бот сервисным токеном: какую машину показать водителю, бот берёт из его роли.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import Document
from app.db.session import get_session
from app.domain import cars as cars_service
from app.domain import documents as documents_service
from app.errors import NotFound
from contracts import DocumentCreate, DocumentDTO

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _dto(document: Document) -> DocumentDTO:
    return DocumentDTO(
        id=document.id,
        car_id=document.car_id,
        title=document.title,
        file_id=document.file_id,
        file_kind=document.file_kind,
        file_name=document.file_name,
        file_size=document.file_size,
        created_by=document.created_by,
        created_at=_utc(document.created_at),
    )


@router.get("/cars/{car_id}/documents", response_model=list[DocumentDTO])
async def list_car_documents(
    car_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[DocumentDTO]:
    if await cars_service.get_car(session, car_id) is None:
        raise NotFound(f"машина {car_id} не найдена")
    rows = await documents_service.list_documents(session, car_id=car_id)
    return [_dto(d) for d in rows]


# Раньше /documents/{document_id}: иначе «common» разбирался бы как id и давал 422.
@router.get("/documents/common", response_model=list[DocumentDTO])
async def list_common_documents(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[DocumentDTO]:
    rows = await documents_service.list_documents(session, car_id=None)
    return [_dto(d) for d in rows]


@router.get("/documents/{document_id}", response_model=DocumentDTO)
async def get_document(
    document_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> DocumentDTO:
    document = await documents_service.get_document(session, document_id)
    if document is None:
        raise NotFound(f"документ {document_id} не найден")
    return _dto(document)


@router.post("/documents", response_model=DocumentDTO, status_code=201)
async def add_document(
    payload: DocumentCreate,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> DocumentDTO:
    document = await documents_service.add_document(
        session,
        car_id=payload.car_id,
        title=payload.title,
        file_id=payload.file_id,
        file_kind=payload.file_kind,
        file_name=payload.file_name,
        file_size=payload.file_size,
        created_by=actor,
    )
    return _dto(document)


@router.delete("/documents/{document_id}", status_code=204, response_model=None)
async def delete_document(
    document_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> Response:
    if not await documents_service.delete_document(session, document_id):
        raise NotFound(f"документ {document_id} не найден")
    return Response(status_code=204)
