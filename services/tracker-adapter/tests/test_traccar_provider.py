"""TraccarProvider: маппинг команд, резолв deviceId, реавторизация при 401, стримы."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.providers.base import TrackerCommand
from app.providers.traccar import COMMAND_MAP, TraccarProvider

DEVICES = [
    {"id": 501, "uniqueId": "9175358042", "name": "139", "status": "online", "lastUpdate": "x"},
    {"id": 502, "uniqueId": "9176603242", "name": "138", "status": "online", "lastUpdate": "x"},
]

RAW_POSITION = {
    "id": 1,
    "deviceId": 501,
    "deviceTime": "2026-09-01T10:00:00+00:00",
    "latitude": 42.87,
    "longitude": 74.57,
    "speed": 10.0,
    "course": 90.0,
    "altitude": 750.0,
    "valid": True,
    "attributes": {
        "ignition": True,
        "motion": False,
        "totalDistance": 5000.0,
        "status": 0xFFFFFFFF,
        "protocol": "h02",
    },
}


def _make_provider(handler, **kwargs) -> TraccarProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://traccar.test", transport=transport)
    return TraccarProvider(
        "http://traccar.test", "svc@example.com", "secret", http_client=client, **kwargs
    )


def _session_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"set-cookie": "JSESSIONID=abc; Path=/"})


# --- маппинг команд -----------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,expected_type",
    [
        (TrackerCommand.ENGINE_STOP, "engineStop"),
        (TrackerCommand.ENGINE_RESUME, "engineResume"),
        (TrackerCommand.ALARM_ARM, "alarmArm"),
        (TrackerCommand.ALARM_DISARM, "alarmDisarm"),
    ],
)
async def test_send_command_maps_our_command_to_traccar_type(cmd, expected_type):
    sent_bodies = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        if request.url.path == "/api/devices":
            return httpx.Response(200, json=DEVICES)
        if request.url.path == "/api/commands/send":
            sent_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"id": 1, **json.loads(request.content)})
        return httpx.Response(404)

    provider = _make_provider(handler)
    result = await provider.send_command("9175358042", cmd)

    assert result["status"] == "sent"
    assert sent_bodies[0] == {"deviceId": 501, "type": expected_type}
    assert COMMAND_MAP[cmd] == expected_type


async def test_send_command_positional_periodic_not_supported():
    """positionPeriodic сознательно не входит в COMMAND_MAP — прошивка ST-901M его игнорирует."""
    assert "positionPeriodic" not in COMMAND_MAP.values()


async def test_send_command_failure_reports_failed_status():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        if request.url.path == "/api/devices":
            return httpx.Response(200, json=DEVICES)
        if request.url.path == "/api/commands/send":
            return httpx.Response(500, text="boom")
        return httpx.Response(404)

    provider = _make_provider(handler)
    result = await provider.send_command("9175358042", TrackerCommand.ENGINE_STOP)

    assert result["status"] == "failed"
    assert result["result"] == "boom"


# --- резолв uniqueId -> deviceId --------------------------------------------------


async def test_resolve_device_id_caches_devices_list():
    calls = {"devices": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        if request.url.path == "/api/devices":
            calls["devices"] += 1
            return httpx.Response(200, json=DEVICES)
        return httpx.Response(404)

    provider = _make_provider(handler)
    first = await provider._resolve_device_id("9175358042")
    second = await provider._resolve_device_id("9176603242")

    assert first == 501
    assert second == 502
    assert calls["devices"] == 1  # второй резолв взял из кэша, без нового запроса


async def test_resolve_device_id_refreshes_cache_on_miss_then_raises():
    calls = {"devices": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        if request.url.path == "/api/devices":
            calls["devices"] += 1
            return httpx.Response(200, json=DEVICES)
        return httpx.Response(404)

    provider = _make_provider(handler)
    with pytest.raises(ValueError):
        await provider._resolve_device_id("unknown-uid")

    assert calls["devices"] == 1  # кэш обновлялся один раз перед тем, как сдаться


# --- реавторизация при 401 ----------------------------------------------------------


async def test_reauthenticates_on_401_and_retries_request():
    session_calls = 0
    devices_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_calls, devices_calls
        if request.url.path == "/api/session":
            session_calls += 1
            return _session_ok(request)
        if request.url.path == "/api/devices":
            devices_calls += 1
            if devices_calls == 1:
                return httpx.Response(401)  # первая попытка — просроченная сессия
            return httpx.Response(200, json=DEVICES)
        return httpx.Response(404)

    provider = _make_provider(handler)
    provider._authenticated = True  # была валидна ранее, но сессия истекла на сервере

    devices = await provider.list_devices()

    assert devices == DEVICES
    assert session_calls == 1  # реавторизация произошла один раз
    assert devices_calls == 2  # первый (401) + повтор после реавторизации


# --- нормализация состояния ----------------------------------------------------------


async def test_get_state_returns_none_when_no_positions():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        if request.url.path == "/api/devices":
            return httpx.Response(200, json=DEVICES)
        if request.url.path == "/api/positions":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    provider = _make_provider(handler)
    assert await provider.get_state("9175358042") is None


async def test_get_state_normalizes_first_position():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        if request.url.path == "/api/devices":
            return httpx.Response(200, json=DEVICES)
        if request.url.path == "/api/positions":
            return httpx.Response(200, json=[RAW_POSITION])
        return httpx.Response(404)

    provider = _make_provider(handler)
    point = await provider.get_state("9175358042")

    assert point is not None
    assert point.external_id == "9175358042"
    assert point.lat == 42.87


# --- поллинг-фолбэк (TRACCAR_WS_ENABLED=0) --------------------------------------------


async def test_poll_stream_yields_normalized_points():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        if request.url.path == "/api/positions":
            return httpx.Response(200, json=[RAW_POSITION])
        return httpx.Response(404)

    provider = _make_provider(handler, poll_interval_seconds=1000)
    gen = provider._poll_stream()
    point = await gen.__anext__()
    await gen.aclose()

    assert point.lat == 42.87
    assert point.engine_blocked is False


# --- WebSocket-стрим: нормализация + реконнект с бэкоффом -----------------------------


class _FakeWebSocket:
    def __init__(self, frames: list[str], error: Exception | None = None) -> None:
        self._frames = frames
        self._error = error

    async def __aenter__(self) -> "_FakeWebSocket":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for frame in self._frames:
            yield frame
        if self._error is not None:
            raise self._error


async def test_ws_stream_yields_normalized_points_from_connected_socket():
    """Кадр {"positions": [...]} нормализуется в NormalizedPoint через полный stream()."""
    frame = json.dumps({"positions": [RAW_POSITION]})

    def ws_connect(url: str, **kwargs: object) -> _FakeWebSocket:
        return _FakeWebSocket([frame])  # без ошибки — цикл переподключается сразу же

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        return httpx.Response(404)

    provider = _make_provider(handler, ws_connect=ws_connect)

    gen = provider.stream()
    point = await gen.__anext__()
    await gen.aclose()

    assert point.external_id == str(RAW_POSITION["deviceId"])
    assert point.lat == RAW_POSITION["latitude"]
    assert point.engine_blocked is False


async def test_ws_stream_reconnects_with_exponential_backoff_on_connect_failure(monkeypatch):
    """Traccar недоступен на каждой попытке соединения — бэкофф должен расти."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("app.providers.traccar.asyncio.sleep", fake_sleep)

    ws_calls: list[str] = []

    def ws_connect(url: str, **kwargs: object) -> _FakeWebSocket:
        ws_calls.append(url)
        raise ConnectionRefusedError("traccar unreachable")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/session":
            return _session_ok(request)
        return httpx.Response(404)

    provider = _make_provider(handler, ws_connect=ws_connect)

    with pytest.raises(asyncio.CancelledError):
        async for _ in provider.stream():
            pass  # соединение никогда не устанавливается — точек не будет

    assert sleeps == [1.0, 2.0]  # экспоненциальный бэкофф между попытками
    assert ws_calls == ["ws://traccar.test/api/socket"] * 2
    assert provider.connected is False
