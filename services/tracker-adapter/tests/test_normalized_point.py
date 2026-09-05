"""Нормализация позиций Traccar в NormalizedPoint — plan/09-traccar-reference.md."""
from __future__ import annotations

from decimal import Decimal

from app.providers.traccar import TraccarProvider


def _provider() -> TraccarProvider:
    return TraccarProvider("http://traccar.test", "svc@example.com", "secret")


def _raw_position(**overrides) -> dict:
    base = {
        "id": 1,
        "deviceId": 139,
        "deviceTime": "2026-09-01T10:00:00+00:00",
        "latitude": 42.8746,
        "longitude": 74.5698,
        "speed": 12.5,
        "course": 180.0,
        "altitude": 800.0,
        "valid": True,
        "attributes": {
            "ignition": True,
            "motion": True,
            "totalDistance": 123456.0,  # метры — Traccar отдаёт в метрах
            "status": 0xFFFFFFFF,
            "io1": 437,
            "io2": 9,
            "io3": 7111,
            "protocol": "h02",
        },
    }
    base.update(overrides)
    return base


def test_normalize_maps_core_fields():
    point = _provider()._normalize(_raw_position(), external_id="9175358042")

    assert point.external_id == "9175358042"
    assert point.lat == 42.8746
    assert point.lon == 74.5698
    assert point.speed_knots == 12.5
    assert point.course == 180.0
    assert point.altitude == 800.0
    assert point.valid is True
    assert point.ignition is True
    assert point.motion is True


def test_normalize_total_distance_converted_to_km():
    point = _provider()._normalize(_raw_position(), external_id="9175358042")
    assert point.total_distance_km == Decimal("123456.0") / Decimal(1000)
    assert point.total_distance_km == Decimal("123.456")


def test_normalize_moves_consumed_attrs_out_of_attributes_and_keeps_rest():
    point = _provider()._normalize(_raw_position(), external_id="9175358042")
    # ignition/motion/totalDistance/status ушли в отдельные поля NormalizedPoint.
    assert "ignition" not in point.attributes
    assert "motion" not in point.attributes
    assert "totalDistance" not in point.attributes
    assert "status" not in point.attributes
    # Остальное (io*, protocol) осталось в attributes.
    assert point.attributes == {"io1": 437, "io2": 9, "io3": 7111, "protocol": "h02"}


def test_engine_blocked_false_for_0xffffffff():
    """0xFFFFFFFF — зажигание вкл, бит 27 = 1 -> не заблокирован."""
    position = _raw_position(attributes={**_raw_position()["attributes"], "status": 0xFFFFFFFF})
    point = _provider()._normalize(position, external_id="9175358042")
    assert point.engine_blocked is False
    assert point.status_raw == hex(0xFFFFFFFF)


def test_engine_blocked_true_for_0xf7fffbff():
    """0xF7FFFBFF — блокировка + зажигание выкл, бит 27 = 0 -> заблокирован."""
    position = _raw_position(attributes={**_raw_position()["attributes"], "status": 0xF7FFFBFF})
    point = _provider()._normalize(position, external_id="9175358042")
    assert point.engine_blocked is True
    assert point.status_raw == hex(0xF7FFFBFF)


def test_normalize_falls_back_to_unique_id_from_device_cache():
    provider = _provider()
    provider._device_by_unique = {"9175358042": 139}
    provider._unique_by_device = {139: "9175358042"}
    point = provider._normalize(_raw_position())  # без явного external_id
    assert point.external_id == "9175358042"


def test_normalize_missing_status_leaves_engine_blocked_none():
    attrs = _raw_position()["attributes"]
    attrs = {k: v for k, v in attrs.items() if k != "status"}
    position = _raw_position(attributes=attrs)
    point = _provider()._normalize(position, external_id="9175358042")
    assert point.engine_blocked is None
    assert point.status_raw is None
