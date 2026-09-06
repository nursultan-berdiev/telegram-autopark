"""Разбор ответа tolom.kg.

Форма снята с живого сервиса 06.09.2026. Госномер, номера постановлений и
коды оплаты обезличены: настоящие однозначно опознают нарушение.
"""
from __future__ import annotations

import copy
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from app.tolom.parser import parse_violations, plate_registered

REAL_WITH_FINES = {
    "currentInfo": {
        "success": True,
        "service": "TRANSPORT_CURRENT_INFO",
        "message": "SUCCESS",
        "data": {
            "govPlate": "01KG000AAA",
            "carTypeName": "легковой",
            "brand": "HYUNDAI",
            "model": "SONATA",
            "steering": "левый",
            "year": "2019",
            "color": "белый",
            "engineVolume": "1999",
            "dateFrom": None,
        },
    },
    "penalties": {
        "success": True,
        "service": "FINES_SEARCH",
        "message": "SUCCESS",
        "data": {
            "bgProtocols": [
                {
                    "paymentCode": "1000000000000000001",
                    "fineAmount": 1000.0,
                    "penaltyAmount": 0.0,
                    "fineAmountToPay": 300.0,
                    "discountDaysLeft": 30,
                    "article": "Ст. 187",
                    "part": "ч. 1",
                    "violationTitle": (
                        "Нарушение «превышение скорости» свыше 10 км/ч, "
                        "но не более 20 км/ч"
                    ),
                    "protocolNumber": "02-08-051-01-7-000001",
                    "plateNumber": "01KG000AAA",
                    "violationDate": "2026-08-30T13:08:59",
                    "violationPlace": " а/д \"Балыкчы-Каракол\" 28.6-й км\n",
                    "deliveryDate": None,
                }
            ],
            "erpnProtocols": [],
        },
    },
    "arestInfo": {"success": False, "message": "NOT_FOUND", "data": None},
    "penaltyBG": {"quantity": 1, "sum": 300.0},
    "penaltyERN": {"quantity": 0, "sum": 0.0},
    "totalPenalties": 1,
    "sumPenalties": 300.0,
}

# Несуществующий номер отвечает HTTP 200 и NOT_FOUND — ровно как чистая машина.
REAL_UNKNOWN_PLATE = {
    "currentInfo": {"success": False, "service": "TRANSPORT_CURRENT_INFO", "message": "NOT_FOUND", "data": None},
    "penalties": {"success": False, "service": "FINES_SEARCH", "message": "NOT_FOUND", "data": None},
    "penaltyBG": {"quantity": 0, "sum": 0.0},
    "penaltyERN": {"quantity": 0, "sum": 0.0},
    "totalPenalties": 0,
    "sumPenalties": 0.0,
}


def _clean(payload: dict) -> dict:
    """Настоящая машина без штрафов: карточка есть, протоколов нет."""
    clean = copy.deepcopy(payload)
    clean["penalties"]["data"] = {"bgProtocols": [], "erpnProtocols": []}
    clean["penaltyBG"] = {"quantity": 0, "sum": 0.0}
    clean["totalPenalties"] = 0
    return clean


# --- номер в реестре --------------------------------------------------------


def test_known_plate_is_registered():
    assert plate_registered(REAL_WITH_FINES) is True


def test_unknown_plate_is_not_registered():
    """Опечатка в госномере не должна выглядеть как машина без штрафов."""
    assert plate_registered(REAL_UNKNOWN_PLATE) is False


def test_clean_car_is_still_registered():
    assert plate_registered(_clean(REAL_WITH_FINES)) is True


# --- разбор нарушений -------------------------------------------------------


def test_parses_both_amounts_and_discount():
    parsed, unparsed = parse_violations(REAL_WITH_FINES)

    assert unparsed == []
    assert len(parsed) == 1
    fine = parsed[0]
    assert fine.external_ref == "02-08-051-01-7-000001"
    assert fine.amount == Decimal("1000")
    assert fine.amount_to_pay == Decimal("300")
    assert fine.discount_days_left == 30


def test_details_go_to_their_own_fields():
    """То, ради чего подключён источник: carcheck этого не отдаёт вовсе."""
    fine = parse_violations(REAL_WITH_FINES)[0][0]

    assert fine.article == "Ст. 187 ч. 1"
    assert "превышение скорости" in fine.violation_title
    assert "Балыкчы-Каракол" in fine.place
    assert fine.payment_code == "1000000000000000001"
    assert fine.protocol_kind == "bg"
    # Распознанная запись примечания не заводит: оно для carcheck и ручного ввода.
    assert fine.note is None
    # Переводы строк и двойные пробелы из ответа в место не уезжают.
    assert "\n" not in fine.place
    assert "  " not in fine.place


def test_delivery_date_absent_means_not_handed():
    """Не вручено — срок скидки не идёт, и выдумывать дату неоткуда."""
    fine = parse_violations(REAL_WITH_FINES)[0][0]

    assert fine.delivery_date is None
    assert fine.discount_days_left == 30


def test_delivery_date_is_a_day_not_a_moment():
    import copy

    payload = copy.deepcopy(REAL_WITH_FINES)
    payload["penalties"]["data"]["bgProtocols"][0]["deliveryDate"] = "2026-09-01T00:00:00"

    fine = parse_violations(payload)[0][0]

    assert fine.delivery_date == date(2026, 9, 1)


def test_local_time_is_kept_local():
    """Время без зоны — местное: иначе ночное нарушение уедет на сутки назад."""
    fine = parse_violations(REAL_WITH_FINES)[0][0]

    assert fine.issued_at.utcoffset() is not None
    assert fine.issued_at.astimezone(timezone.utc) == datetime(
        2026, 8, 30, 7, 8, 59, tzinfo=timezone.utc
    )


def test_clean_car_gives_no_violations():
    parsed, unparsed = parse_violations(_clean(REAL_WITH_FINES))

    assert (parsed, unparsed) == ([], [])


def test_unknown_plate_gives_no_violations():
    assert parse_violations(REAL_UNKNOWN_PLATE) == ([], [])


def test_record_without_protocol_number_is_unparsed():
    """Без номера постановления следующий прогон завёл бы дубль."""
    payload = copy.deepcopy(REAL_WITH_FINES)
    payload["penalties"]["data"]["bgProtocols"][0].pop("protocolNumber")

    parsed, unparsed = parse_violations(payload)

    assert parsed == []
    assert len(unparsed) == 1


# --- ERPN: живого образца формы не было -------------------------------------


def test_erpn_protocol_is_imported_with_known_keys():
    payload = copy.deepcopy(REAL_WITH_FINES)
    payload["penalties"]["data"]["erpnProtocols"] = [
        {
            "protocolNumber": "02-08-051-01-7-000002",
            "fineAmount": 5000.0,
            "violationDate": "2026-09-01T10:00:00",
            "article": "Ст. 200",
        }
    ]
    payload["totalPenalties"] = 2

    parsed, _ = parse_violations(payload)

    refs = [p.external_ref for p in parsed]
    assert "02-08-051-01-7-000002" in refs
    erpn = next(p for p in parsed if p.external_ref.endswith("000002"))
    assert erpn.amount == Decimal("5000")
    # Скидки в записи нет — подставлять её неоткуда.
    assert erpn.amount_to_pay is None


def test_unfamiliar_record_keeps_raw_json_in_note():
    """Неизвестную форму не выдумываем: показываем сырьё, чтобы снять формат."""
    payload = copy.deepcopy(REAL_WITH_FINES)
    payload["penalties"]["data"]["erpnProtocols"] = [
        {"protocolNumber": "02-08-051-01-7-000003", "someNewField": "значение"}
    ]
    payload["totalPenalties"] = 2

    parsed, _ = parse_violations(payload)

    raw = next(p for p in parsed if p.external_ref.endswith("000003"))
    assert "не разобрано" in raw.note
    assert "someNewField" in raw.note
    assert raw.amount is None
    # Вид протокола известен из ключа ответа даже у незнакомой записи —
    # по нему мы и узнаем, что живой ERPN наконец появился.
    assert raw.protocol_kind == "erpn"


def test_count_mismatch_is_logged(caplog):
    """Сервис насчитал больше, чем мы разобрали, — часть нарушений не видна."""
    payload = copy.deepcopy(REAL_WITH_FINES)
    payload["totalPenalties"] = 5

    with caplog.at_level(logging.WARNING):
        parse_violations(payload)

    assert "обещал 5" in caplog.text


def test_broken_payload_does_not_raise():
    assert parse_violations({"penalties": {"data": "мусор"}}) == ([], [])
    assert parse_violations([]) == ([], [])
    assert parse_violations(None) == ([], [])


def test_string_days_left_is_accepted_not_lost():
    """Число может приехать строкой — молча терять скидку нельзя."""
    payload = copy.deepcopy(REAL_WITH_FINES)
    payload["penalties"]["data"]["bgProtocols"][0]["discountDaysLeft"] = "30"

    fine = parse_violations(payload)[0][0]

    assert fine.discount_days_left == 30


def test_unparsable_days_left_is_logged(caplog):
    payload = copy.deepcopy(REAL_WITH_FINES)
    payload["penalties"]["data"]["bgProtocols"][0]["discountDaysLeft"] = "тридцать"

    with caplog.at_level(logging.WARNING):
        fine = parse_violations(payload)[0][0]

    assert fine.discount_days_left is None
    assert "discountDaysLeft" in caplog.text
