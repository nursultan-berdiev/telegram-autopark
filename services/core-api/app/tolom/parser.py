"""Разбор ответа tolom.kg.

Форма снята с живого сервиса 06.09.2026 (документации у него нет):

    {"currentInfo": {"success": true, "service": "TRANSPORT_CURRENT_INFO",
                     "message": "SUCCESS", "data": {"brand": "…", "model": "…"}},
     "penalties":   {"success": true, "service": "FINES_SEARCH",
                     "message": "SUCCESS",
                     "data": {"bgProtocols": [{"paymentCode": "…",
                                               "fineAmount": 1000.0,
                                               "penaltyAmount": 0.0,
                                               "fineAmountToPay": 300.0,
                                               "discountDaysLeft": 30,
                                               "article": "Ст. 187", "part": "ч. 1",
                                               "violationTitle": "…",
                                               "protocolNumber": "02-08-051-01-7-000000",
                                               "violationDate": "2026-08-30T13:08:59",
                                               "violationPlace": "…"}],
                              "erpnProtocols": []}},
     "penaltyBG": {"quantity": 2, "sum": 600.0}, "penaltyERN": {"quantity": 0, "sum": 0.0},
     "totalPenalties": 2, "sumPenalties": 600.0}

Три вещи, которые нельзя упростить:

1. **Неизвестный номер отвечает HTTP 200** и `penalties.success=false,
   message=NOT_FOUND` — ровно как машина без штрафов. Различает их только
   `currentInfo`: у настоящей машины там марка и модель. Иначе опечатка в
   госномере навсегда выглядела бы как чистая машина.
2. **Сумм две.** `fineAmount` — полная, `fineAmountToPay` — со скидкой и
   только `discountDaysLeft` дней. Хранить одну «сумму» нельзя: после
   истечения скидки любая из них по отдельности врёт.
3. **Протоколов тоже два вида.** `bgProtocols` — автофиксация («Безопасный
   город»), `erpnProtocols` — протоколы из электронного реестра. Живого
   образца ERPN у нас не было, поэтому он разбирается теми же ключами, а всё
   неузнанное уходит в примечание сырьём и в лог — не выдумывая полей.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.fines_sources import ParsedViolation, normalize_amount, normalize_date

log = logging.getLogger(__name__)

PROTOCOL_KEYS = ("bgProtocols", "erpnProtocols")
NOTE_LIMIT = 500


def _section(payload: Any, name: str) -> dict:
    section = payload.get(name) if isinstance(payload, dict) else None
    return section if isinstance(section, dict) else {}


def plate_registered(payload: Any) -> bool:
    """Есть ли номер в реестре сервиса.

    «Штрафов нет» и «такого номера нет» на уровне penalties неразличимы, и
    принять второе за первое значило бы навсегда ослепнуть по этой машине.
    """
    return bool(_section(payload, "currentInfo").get("success"))


def expected_counts(payload: Any) -> int | None:
    """Сколько нарушений обещает сам сервис в итогах — для сверки с разобранным."""
    total = payload.get("totalPenalties") if isinstance(payload, dict) else None
    return total if isinstance(total, int) else None


def _note(record: dict) -> str | None:
    """Статья, название нарушения и место — то, чего нет у carcheck."""
    article = " ".join(
        str(record[key]).strip()
        for key in ("article", "part")
        if record.get(key) not in (None, "")
    )
    title = str(record.get("violationTitle") or "").strip()
    place = " ".join(str(record.get("violationPlace") or "").split())

    known = [part for part in (article, title) if part]
    head = " — ".join(known)
    if not known:
        # Форма записи незнакома (ждём живой ERPN): показываем сырьё, а не
        # догадку — по нему потом снимем формат.
        head = "не разобрано: " + json.dumps(record, ensure_ascii=False)
    text = f"{head} · {place}" if place else head
    return text[:NOTE_LIMIT] or None


def _days_left(raw: Any, ref: Any) -> int | None:
    """Остаток дней скидки.

    Число может приехать строкой — на потерю значения молчать нельзя, как и на
    расхождение итогов ниже: обе потери снаружи выглядят как «скидки нет».
    """
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        log.warning(
            "discountDaysLeft по %s пришёл в неожиданном виде: %r", ref, raw
        )
        return None


def parse_violations(payload: Any) -> tuple[list[ParsedViolation], list[dict]]:
    """Возвращает (разобранные, неразобранные).

    Запись без номера постановления не импортируется: без него следующий
    прогон завёл бы дубль.
    """
    data = _section(payload, "penalties").get("data")
    parsed: list[ParsedViolation] = []
    unparsed: list[dict] = []
    if not isinstance(data, dict):
        return parsed, unparsed

    for key in PROTOCOL_KEYS:
        records = data.get(key)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            ref = record.get("protocolNumber")
            if ref in (None, ""):
                unparsed.append(record)
                log.warning("%s: запись без номера постановления пропущена", key)
                continue
            discount_days = _days_left(record.get("discountDaysLeft"), ref)
            parsed.append(
                ParsedViolation(
                    external_ref=str(ref)[:64],
                    issued_at=normalize_date(record.get("violationDate")),
                    amount=normalize_amount(record.get("fineAmount")),
                    note=_note(record),
                    amount_to_pay=normalize_amount(record.get("fineAmountToPay")),
                    discount_days_left=discount_days,
                )
            )

    expected = expected_counts(payload)
    if expected is not None and expected != len(parsed) + len(unparsed):
        # Сервис сам посчитал иначе — значит часть нарушений лежит там, куда
        # мы не смотрим. Молчать нельзя: пропажа выглядит как чистая машина.
        log.warning(
            "tolom обещал %d нарушений, разобрано %d (+%d неразобранных)",
            expected,
            len(parsed),
            len(unparsed),
        )
    return parsed, unparsed
