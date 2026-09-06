"""Разбор ответа carcheck.gov.kg.

Форма ответа снята с живого сервиса 06.09.2026:

    {"vehicle":    {"success": true, "message": "", "data": {...}},
     "violations": {"success": true, "message": "", "data": [
        {"violationType": "AFP",
         "protocolNumber": "02-08-051-01-7-382542",
         "violationDate": "2026-08-10T20:06:04"}]}}

Список нарушений лежит на уровень глубже имени ключа, а суммы в ответе нет
вовсе — только тип, номер постановления и время. Оператор смотрит сумму сам,
задача бота — сообщить, что штраф появился.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings

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
# Сервис отдаёт время без зоны, и это местное время страны, где он работает.
# Отдать его на откуп зоне сервера нельзя: ночное нарушение уехало бы на
# соседние сутки, а по датам считается окно правила «N штрафов за период».
# Зона берётся из настроек, а не зашивается смещением: одно место правки.
SERVICE_TZ = ZoneInfo(settings.timezone)


@dataclass(frozen=True)
class ParsedViolation:
    external_ref: str
    issued_at: datetime | None
    amount: Decimal | None
    note: str | None


def pick(record: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


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


def normalize_amount(raw: Any) -> Decimal | None:
    if raw in (None, ""):
        return None
    text = "".join(ch for ch in str(raw) if not ch.isspace())
    if not text or any(ch not in "0123456789.,-" for ch in text):
        return None

    # Формат определяем по последнему разделителю, а не по наличию точки:
    # «3.000,50» и «3,000.50» — одна сумма, но по точке читаются наоборот.
    last = max(text.rfind("."), text.rfind(","))
    if last == -1:
        normalized = text
    else:
        tail = text[last + 1 :]
        head = text[:last].replace(".", "").replace(",", "")
        # Ровно три цифры после разделителя — разряд тысяч («1.234»):
        # дробная часть такой длины в деньгах не встречается.
        normalized = head + tail if len(tail) == 3 else f"{head}.{tail}"
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def normalize_date(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=SERVICE_TZ)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SERVICE_TZ)


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
