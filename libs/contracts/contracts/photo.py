"""DTO фотоконтроля состояния машины."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from .common import DTO

# Ширина колонок photo_inspections: длиннее Postgres отказал бы 500-й, а не 422.
PHOTO_FILE_ID_MAX = 256
PHOTO_COMMENT_MAX = 2000


class PhotoInspectionCreate(DTO):
    """Осмотр от водителя. Кто сдаёт — сервер берёт из X-TG-User-Id, машину — из его записи."""

    front_file_id: str = Field(min_length=1, max_length=PHOTO_FILE_ID_MAX)
    rear_file_id: str = Field(min_length=1, max_length=PHOTO_FILE_ID_MAX)
    interior_front_file_id: str = Field(min_length=1, max_length=PHOTO_FILE_ID_MAX)
    interior_rear_file_id: str = Field(min_length=1, max_length=PHOTO_FILE_ID_MAX)
    has_issues: bool = False
    comment: str | None = Field(default=None, max_length=PHOTO_COMMENT_MAX)


class PhotoInspectionDTO(DTO):
    """Сданный осмотр: 4 фото (Telegram file_id) и отчёт; имя и номер — для подписи."""

    id: int
    driver_id: int
    driver_name: str | None = None
    car_id: int | None = None
    car_plate: str | None = None
    front_file_id: str
    rear_file_id: str
    interior_front_file_id: str
    interior_rear_file_id: str
    has_issues: bool
    comment: str | None = None
    created_at: datetime | None = None


class PhotoInspectionResult(DTO):
    """Ответ на сдачу: принятый осмотр и новый срок (локальная дата парка)."""

    inspection: PhotoInspectionDTO
    next_due_on: date


class PhotoStatusDTO(DTO):
    """Статус фотоконтроля водителя: срок и просрочка в днях (0 — не просрочен)."""

    driver_id: int
    driver_name: str
    car_id: int
    car_plate: str
    next_due_on: date
    overdue_days: int
    last_inspection_id: int | None = None
    last_inspection_at: datetime | None = None
