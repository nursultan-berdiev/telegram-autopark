"""Разбор ответа carcheck: тесты на фактических ответах сервиса."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal

from app.carcheck.parser import (
    extract_list,
    normalize_amount,
    normalize_date,
    parse_violations,
)

# Форма ответа снята с живого сервиса 06.09.2026. Номера машин и
# постановлений обезличены: настоящие однозначно опознают нарушение.
REAL_EMPTY = {
    "vehicle": {
        "success": True,
        "message": "",
        "data": {
            "govPlate": "01KG100AAA",
            "steering": "левый",
            "year": 2019,
            "brand": "HYUNDAI",
            "model": "SONATA",
            "color": "белый",
            "carTypeName": "легковой",
            "engineVolume": "1999",
            "motorType": "газ",
            "tintingWindow": None,
            "ownersPeriod": [{"dateFrom": "2023-09-24", "dateTo": ""}],
            "arestExists": False,
        },
    },
    "violations": {"success": True, "message": "", "data": []},
}

# Шесть штрафов, ни у одного нет суммы — так отдаёт сервис.
REAL_WITH_FINES = copy.deepcopy(REAL_EMPTY)
REAL_WITH_FINES["violations"]["data"] = [
    {"violationType": "AFP", "protocolNumber": "00-00-000-00-0-100001", "violationDate": "2026-08-10T20:06:04"},
    {"violationType": "AFP", "protocolNumber": "00-00-000-00-0-100002", "violationDate": "2026-08-09T23:29:49.098"},
    {"violationType": "AFP", "protocolNumber": "00-00-000-00-0-100003", "violationDate": "2026-08-10T00:16:28"},
    {"violationType": "AFP", "protocolNumber": "00-00-000-00-0-100004", "violationDate": "2026-08-10T19:53:13"},
    {"violationType": "AFP", "protocolNumber": "00-00-000-00-0-100005", "violationDate": "2026-08-11T15:20:36"},
    {"violationType": "AFP", "protocolNumber": "00-00-000-00-0-100006", "violationDate": "2026-08-21T06:45:38"},
]


def test_empty_response_yields_nothing():
    assert extract_list(REAL_EMPTY) == []
    assert parse_violations(REAL_EMPTY) == ([], [])


def test_all_six_real_fines_are_parsed():
    """Список лежит в violations.data — на уровень глубже имени ключа."""
    parsed, unparsed = parse_violations(REAL_WITH_FINES)

    assert len(parsed) == 6
    assert unparsed == []
    assert parsed[0].external_ref == "00-00-000-00-0-100001"
    assert parsed[0].note == "AFP", "тип нарушения — единственное описание от сервиса"


def test_amount_is_absent_and_not_invented():
    parsed, _ = parse_violations(REAL_WITH_FINES)

    assert all(v.amount is None for v in parsed)


def test_time_is_read_as_bishkek_not_server_zone():
    """00:16 по Бишкеку — это предыдущие сутки в UTC."""
    parsed, _ = parse_violations(REAL_WITH_FINES)

    midnight = next(v for v in parsed if v.external_ref.endswith("100003"))
    assert midnight.issued_at.astimezone(timezone.utc) == datetime(
        2026, 8, 9, 18, 16, 28, tzinfo=timezone.utc
    )


def test_milliseconds_do_not_break_parsing():
    parsed, _ = parse_violations(REAL_WITH_FINES)

    with_ms = next(v for v in parsed if v.external_ref.endswith("100002"))
    assert with_ms.issued_at.astimezone(timezone.utc).hour == 17


def test_vehicle_card_is_not_taken_for_violations():
    """В карточке машины свой вложенный список — он не нарушения."""
    only_vehicle = {"vehicle": REAL_EMPTY["vehicle"]}

    assert extract_list(only_vehicle) == []
    assert parse_violations(only_vehicle) == ([], [])


def test_violation_without_ref_is_reported_not_dropped():
    payload = {"violations": {"data": [{"violationType": "AFP"}]}}

    parsed, unparsed = parse_violations(payload)

    assert parsed == []
    assert len(unparsed) == 1, "иначе повторный прогон завёл бы дубль"


def test_list_found_under_unknown_container_name():
    payload = {"неизвестный": {"конверт": [{"protocolNumber": "AM9"}]}}

    assert len(extract_list(payload)) == 1


def test_nested_list_not_hidden_by_sibling_object():
    payload = {"violations": [{"total": 2}, [{"protocolNumber": "АМ1"}]]}

    refs = [r.get("protocolNumber") for r in extract_list(payload)]
    assert "АМ1" in refs


def test_amount_format_read_by_last_separator():
    assert normalize_amount("3 000,50") == Decimal("3000.50")
    assert normalize_amount("3,000.50") == Decimal("3000.50")
    assert normalize_amount("3.000,50") == Decimal("3000.50")
    assert normalize_amount("1.234") == Decimal("1234")
    assert normalize_amount("оплачено") is None
    assert normalize_amount("3000 сом") is None


def test_dotted_date_is_midnight_bishkek():
    assert normalize_date("17.08.2026").astimezone(timezone.utc) == datetime(
        2026, 8, 16, 18, 0, tzinfo=timezone.utc
    )


def test_explicit_zone_is_respected():
    assert normalize_date("2026-08-17T10:00:00Z").astimezone(timezone.utc) == datetime(
        2026, 8, 17, 10, 0, tzinfo=timezone.utc
    )


def test_garbage_date_is_none():
    assert normalize_date("позавчера") is None
    assert normalize_date(None) is None
