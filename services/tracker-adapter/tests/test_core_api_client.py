"""CoreApiClient: сериализация батча (ISO datetime) и Bearer INGEST_TOKEN."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.clients.core_api import CoreApiClient
from app.providers.base import NormalizedPoint


def _point(**overrides) -> NormalizedPoint:
    base = dict(
        external_id="9175358042",
        ts=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        server_ts=datetime(2026, 9, 1, 10, 0, 5, tzinfo=timezone.utc),
        lat=42.87,
        lon=74.57,
        speed_knots=10.0,
        course=90.0,
        altitude=750.0,
        valid=True,
        ignition=True,
        motion=False,
        total_distance_km=Decimal("123.456"),
        engine_blocked=False,
        status_raw="0xffffffff",
        attributes={"protocol": "h02"},
    )
    base.update(overrides)
    return NormalizedPoint(**base)


async def test_push_telemetry_batch_sends_bearer_token_and_iso_datetimes():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    core_api = CoreApiClient("http://core-api.test", "ingest-secret", http_client=client)

    await core_api.push_telemetry_batch([_point()])

    assert captured["url"] == "http://core-api.test/telemetry/batch"
    assert captured["auth"] == "Bearer ingest-secret"
    body = captured["json"]
    assert len(body) == 1
    assert body[0]["ts"] == "2026-09-01T10:00:00+00:00"
    assert body[0]["server_ts"] == "2026-09-01T10:00:05+00:00"
    assert body[0]["total_distance_km"] == "123.456"
    assert body[0]["external_id"] == "9175358042"


async def test_push_telemetry_batch_raises_on_http_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="core-api down")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    core_api = CoreApiClient("http://core-api.test", "ingest-secret", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await core_api.push_telemetry_batch([_point()])
