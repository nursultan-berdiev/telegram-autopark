"""DTO документов машин и общих документов парка."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import DTO

# Пределы = ширина колонок documents: длиннее Postgres отказал бы 500-й, а не 422.
# Название бот проверяет до того, как попросит файл, имя файла — обрезает.
DOCUMENT_MAX_TITLE = 128
DOCUMENT_MAX_FILE_NAME = 256
DOCUMENT_MAX_FILE_ID = 256


class DocumentCreate(DTO):
    car_id: int | None = None  # None — общий документ парка
    title: str  # пустоту и длину после strip проверяет домен
    file_id: str = Field(min_length=1, max_length=DOCUMENT_MAX_FILE_ID)
    file_kind: Literal["document", "photo"]
    file_name: str | None = Field(default=None, max_length=DOCUMENT_MAX_FILE_NAME)
    file_size: int | None = Field(default=None, ge=0)


class DocumentDTO(DTO):
    id: int
    car_id: int | None = None
    title: str
    file_id: str
    file_kind: str
    file_name: str | None = None
    file_size: int | None = None
    created_by: int
    created_at: datetime | None = None
