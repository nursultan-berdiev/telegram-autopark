"""Базовые типы контрактов."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class DTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Role(str, Enum):
    admin = "admin"
    driver = "driver"
    guest = "guest"


class Problem(DTO):
    """Единый формат ошибки домена."""

    detail: str
