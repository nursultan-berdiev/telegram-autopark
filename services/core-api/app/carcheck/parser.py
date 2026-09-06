"""Разбор ответа carcheck.gov.kg.

Форма ответа снята с живого сервиса 06.09.2026:

    {"vehicle":    {"success": true, "message": "", "data": {...}},
     "violations": {"success": true, "message": "", "data": [
        {"violationType": "AFP",
         "protocolNumber": "02-08-051-01-7-382542",
         "violationDate": "2026-08-10T20:06:04"}]}}

Список нарушений лежит на уровень глубже имени ключа, а суммы в ответе нет
вовсе — только тип, номер постановления и время. Сумму отдаёт второй источник
(app/tolom), общие для обоих типы и нормализация — в app/fines_sources.
"""
from __future__ import annotations

from typing import Any

from app.fines_sources import (
    ParsedViolation,
    normalize_amount,
    normalize_date,
    pick,
)

LIST_KEYS = ("violations", "items", "data", "content", "result", "records")
REF_KEYS = (
    "protocolNumber",
    "resolutionNumber",
    "decisionNumber",
    "docNumber",
    "number",
    "seriaNumber",
    "id",
)
AMOUNT_KEYS = ("amount", "sum", "penaltySum", "fineAmount", "totalAmount", "debt")
DATE_KEYS = (
    "violationDate",
    "issuedAt",
    "protocolDate",
    "createdAt",
    "date",
    "decisionDate",
)
NOTE_KEYS = (
    "articleName",
    "violationName",
    "article",
    "description",
    "place",
    "address",
    "violationType",
)

# Защита от бесконечной рекурсии по произвольному JSON.
MAX_DEPTH = 8
def plate_registered(payload: Any) -> bool:
    """Есть ли номер в реестре сервиса.

    Смотрим на данные карточки, а не на флаг `success`: у неизвестного номера
    сервис отвечает `{"vehicle": {"success": true, "message": "Запись не
    найдена", "data": null}}` — то есть успехом. На текст сообщения тоже
    полагаться нельзя, он меняется вместе с локалью сайта (снято с живого
    сервиса 07.09.2026).
    """
    vehicle = payload.get("vehicle") if isinstance(payload, dict) else None
    return bool(isinstance(vehicle, dict) and vehicle.get("data"))


def looks_like_violation(record: Any) -> bool:
    # Только номер постановления: суммы в ответе нет, и требовать её значило бы
    # отбрасывать настоящие нарушения.
    return isinstance(record, dict) and pick(record, REF_KEYS) is not None


def extract_list(payload: Any, depth: int = 0) -> list[dict]:
    if depth > MAX_DEPTH:
        return []
    if isinstance(payload, list):
        objects = [x for x in payload if isinstance(x, dict)]
        nested: list[dict] = []
        for item in payload:
            if isinstance(item, list):
                nested.extend(extract_list(item, depth + 1))
        return objects + nested
    if not isinstance(payload, dict):
        return []

    # Сервис назвал контейнер понятно — берём всё, что в нём лежит, включая
    # записи без номера: их надо показать как неразобранные, а не потерять.
    for key in LIST_KEYS:
        if key in payload:
            found = extract_list(payload[key], depth + 1)
            if found:
                return found
    # Дальше идёт угадывание по дереву, и тут нужны доказательства: иначе за
    # нарушения сойдут периоды владения из карточки машины.
    for value in payload.values():
        found = [r for r in extract_list(value, depth + 1) if looks_like_violation(r)]
        if found:
            return found
    return []


def parse_violations(payload: Any) -> tuple[list[ParsedViolation], list[dict]]:
    """Возвращает (разобранные, неразобранные).

    Нарушение без номера постановления не импортируется: без него следующий
    прогон завёл бы дубль.
    """
    parsed: list[ParsedViolation] = []
    unparsed: list[dict] = []
    for item in extract_list(payload):
        ref = pick(item, REF_KEYS)
        if ref is None:
            unparsed.append(item)
            continue
        note = pick(item, NOTE_KEYS)
        parsed.append(
            ParsedViolation(
                external_ref=str(ref)[:64],
                issued_at=normalize_date(pick(item, DATE_KEYS)),
                amount=normalize_amount(pick(item, AMOUNT_KEYS)),
                note=str(note)[:500] if note is not None else None,
            )
        )
    return parsed, unparsed
