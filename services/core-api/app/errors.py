"""Ошибки домена и их отображение в HTTP-коды."""
from __future__ import annotations


class DomainError(Exception):
    """Нарушение бизнес-правила: 409 по умолчанию, 404/422 — явно."""

    def __init__(self, detail: str, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class NotFound(DomainError):
    def __init__(self, detail: str = "не найдено") -> None:
        super().__init__(detail, status_code=404)


class Conflict(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, status_code=409)
