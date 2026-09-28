"""Общее для источников штрафов: исход проверки, разобранное нарушение, нормализация.

Источников два — carcheck.gov.kg (браузер, только факт нарушения) и tolom.kg
(HTTP, ещё и суммы). Общими остаются три вещи, и каждая из них уже стоила
отдельного бага, если её продублировать:

* исход проверки, где отказ сервиса отделён и от сбоя, и от «штрафов нет»;
* приведение времени без зоны к местному, а не к зоне сервера;
* разбор суммы, где «1.234» — это разряд тысяч, а не дробная часть.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.config import settings

# Сервисы отдают время без зоны, и это местное время страны, где они работают.
# Отдать его на откуп зоне сервера нельзя: ночное нарушение уехало бы на
# соседние сутки, а по датам считается окно правила «N штрафов за период».
# Зона берётся из настроек, а не зашивается смещением: одно место правки.
SERVICE_TZ = ZoneInfo(settings.timezone)


@dataclass(frozen=True)
class CheckResult:
    """Исход проверки одного номера.

    `refused` отделён от неуспеха намеренно: сервис ответил и отказал —
    это не сбой сети и не отсутствие штрафов.

    `plate_known=False` означает, что номера нет в реестре сервиса: у нас
    опечатка в госномере, и штрафы по этой машине не увидит никто и никогда.
    Молчаливо считать это чистой машиной нельзя.
    """

    plate: str
    payload: dict[str, object] | None = None
    refused: str | None = None
    error: str | None = None
    plate_known: bool = True

    @property
    def ok(self) -> bool:
        return self.payload is not None


class Checker(Protocol):
    def check(self, plate: str) -> CheckResult: ...


@dataclass(frozen=True)
class ParsedViolation:
    """Нарушение в терминах домена.

    Суммы необязательны: carcheck не отдаёт их вовсе, и требовать сумму
    значило бы отбрасывать настоящие нарушения.
    """

    external_ref: str
    issued_at: datetime | None
    amount: Decimal | None
    note: str | None
    # Со скидкой платят меньше и только в срок; полная сумма остаётся в amount,
    # иначе после истечения скидки в базе лежала бы неверная цифра.
    amount_to_pay: Decimal | None = None
    discount_days_left: int | None = None
    # Подробности: у carcheck их нет вовсе, и пустое здесь означает «источник
    # не знает», а не «у нарушения этого нет».
    article: str | None = None
    violation_title: str | None = None
    place: str | None = None
    payment_code: str | None = None
    protocol_kind: str | None = None
    # Дата вручения постановления: от неё идёт срок скидки. Пусто — не вручено.
    delivery_date: date | None = None


def pick(record: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


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


def normalize_day(raw: Any) -> date | None:
    """Юридический срок — это день, а не момент.

    Хранить его временем значит подарить себе сдвиг на сутки при первой же
    смене часового пояса ровно там, где мы печатаем голую дату.
    """
    parsed = normalize_date(raw)
    return parsed.date() if parsed is not None else None


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
