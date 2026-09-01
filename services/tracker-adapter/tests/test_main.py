"""Command-API адаптера: Bearer ADAPTER_TOKEN, прозрачная передача результата провайдера.

httpx.ASGITransport не гоняет lifespan (только http-scope) — поэтому app.state.provider
проставляется в тестах вручную, без запуска реального TraccarProvider/IngestWorker.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.main import app
from app.providers.base import NormalizedPoint, TrackerCommand, TrackerProvider


class FakeProvider(TrackerProvider):
    name = "fake"

    def __init__(self) -> None:
        self.sent: list[tuple[str, TrackerCommand, dict | None]] = []
        self.command_result: dict = {"status": "sent", "result": "S20,OK"}
        self.state_point: NormalizedPoint | None = None
        self.connected = True

    async def list_devices(self) -> list[dict]:
        return []

    async def get_state(self, external_id: str) -> NormalizedPoint | None:
        return self.state_point

    async def stream(self):  # pragma: no cover - не используется в этих тестах
        if False:
            yield None

    async def send_command(self, external_id, cmd, params=None) -> dict:
        self.sent.append((external_id, cmd, params))
        return self.command_result


@pytest.fixture
def fake_provider():
    provider = FakeProvider()
    app.state.provider = provider
    yield provider
    del app.state.provider


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


AUTH = {"Authorization": "Bearer adapter-test-token"}


async def test_commands_requires_bearer_token(client, fake_provider):
    resp = await client.post(
        "/devices/9175358042/commands", json={"type": "engine_stop"}
    )
    assert resp.status_code == 401
    assert fake_provider.sent == []


async def test_commands_rejects_wrong_token(client, fake_provider):
    resp = await client.post(
        "/devices/9175358042/commands",
        json={"type": "engine_stop"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


async def test_commands_passes_through_provider_result(client, fake_provider):
    fake_provider.command_result = {"status": "sent", "result": "S20,OK"}

    resp = await client.post(
        "/devices/9175358042/commands",
        json={"type": "engine_stop"},
        headers=AUTH,
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "sent", "result": "S20,OK"}
    assert fake_provider.sent == [("9175358042", TrackerCommand.ENGINE_STOP, None)]


async def test_commands_passes_through_failed_status():
    provider = FakeProvider()
    provider.command_result = {"status": "failed", "result": "timeout"}
    app.state.provider = provider
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/devices/9175358042/commands",
                json={"type": "alarm_arm"},
                headers=AUTH,
            )
    finally:
        del app.state.provider

    assert resp.status_code == 200
    assert resp.json() == {"status": "failed", "result": "timeout"}


async def test_state_requires_token(client, fake_provider):
    resp = await client.get("/devices/9175358042/state")
    assert resp.status_code == 401


async def test_state_returns_null_when_no_position(client, fake_provider):
    fake_provider.state_point = None
    resp = await client.get("/devices/9175358042/state", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() is None


async def test_state_returns_normalized_point(client, fake_provider):
    fake_provider.state_point = NormalizedPoint(
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
        total_distance_km=None,
        engine_blocked=False,
        status_raw="0xffffffff",
    )

    resp = await client.get("/devices/9175358042/state", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["external_id"] == "9175358042"
    assert body["ts"] == "2026-09-01T10:00:00+00:00"
    assert body["lat"] == 42.87


async def test_health_does_not_require_token(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["ingest"] is False  # lifespan не запускался в тесте


async def test_health_reports_provider_connected_state(client, fake_provider):
    fake_provider.connected = True
    resp = await client.get("/health")
    assert resp.json()["traccar"] is True


async def test_unknown_device_returns_failed_not_500(client, fake_provider):
    """Неизвестный uniqueId — понятный отказ в теле, а не 500 обрывком текста."""

    async def _boom(external_id, command, params=None):
        raise ValueError(f"device {external_id} not found")

    fake_provider.send_command = _boom

    response = await client.post(
        "/devices/нет-такого/commands",
        json={"type": "engine_stop"},
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "не найдено" in body["result"]
