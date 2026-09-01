"""Провайдер Traccar/H02 — все факты (эндпоинты, коды команд, биты) из plan/09-traccar-reference.md.

Не угадывать коды команд/биты — расширять только по подтверждённым фактам из справочника.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import websockets

from app.providers.base import NormalizedPoint, TrackerCommand, TrackerProvider

logger = logging.getLogger(__name__)

# Подтверждено на пилоте (139, ST-901M) — plan/09-traccar-reference.md.
# positionPeriodic (интервал) сюда сознательно не входит: прошивка ST-901M его игнорирует.
COMMAND_MAP: dict[TrackerCommand, str] = {
    TrackerCommand.ENGINE_STOP: "engineStop",
    TrackerCommand.ENGINE_RESUME: "engineResume",
    TrackerCommand.ALARM_ARM: "alarmArm",
    TrackerCommand.ALARM_DISARM: "alarmDisarm",
}

_WS_BACKOFF_INITIAL = 1.0
_WS_BACKOFF_MAX = 60.0


class TraccarProvider(TrackerProvider):
    """REST + WebSocket клиент к Traccar."""

    name = "traccar"

    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        *,
        ws_enabled: bool = True,
        poll_interval_seconds: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
        # Инъекция для тестов — реальный websockets.connect бьёт по сети.
        ws_connect: Callable[..., Any] = websockets.connect,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user = user
        self._password = password
        self._ws_enabled = ws_enabled
        self._poll_interval_seconds = poll_interval_seconds
        self._client = http_client or httpx.AsyncClient(base_url=self._base_url, timeout=15.0)
        self._ws_connect = ws_connect
        self._authenticated = False
        # uniqueId -> Traccar numeric deviceId (и обратно — для нормализации WS-кадров).
        self._device_by_unique: dict[str, int] = {}
        self._unique_by_device: dict[int, str] = {}
        # Для /health: живо ли соединение с Traccar прямо сейчас.
        self.connected = False

    # --- аутентификация -----------------------------------------------------

    async def _authenticate(self) -> None:
        resp = await self._client.post(
            "/api/session",
            data={"email": self._user, "password": self._password},
        )
        resp.raise_for_status()
        self._authenticated = True

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not self._authenticated:
            await self._authenticate()
        resp = await self._client.request(method, url, **kwargs)
        if resp.status_code == 401:
            # Сессия истекла/невалидна — реавторизуемся и повторяем один раз.
            self._authenticated = False
            await self._authenticate()
            resp = await self._client.request(method, url, **kwargs)
        return resp

    def _cookie_header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self._client.cookies.items())

    def _ws_url(self) -> str:
        return self._base_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/socket"

    # --- устройства -----------------------------------------------------------

    async def list_devices(self) -> list[dict]:
        resp = await self._request("GET", "/api/devices")
        resp.raise_for_status()
        devices: list[dict] = resp.json()
        self._device_by_unique = {d["uniqueId"]: d["id"] for d in devices}
        self._unique_by_device = {d["id"]: d["uniqueId"] for d in devices}
        return devices

    async def _resolve_device_id(self, external_id: str) -> int:
        if external_id not in self._device_by_unique:
            await self.list_devices()  # промах кэша — обновляем целиком
        if external_id not in self._device_by_unique:
            raise ValueError(f"unknown Traccar device uniqueId={external_id!r}")
        return self._device_by_unique[external_id]

    # --- команды ---------------------------------------------------------------

    async def send_command(
        self,
        external_id: str,
        cmd: TrackerCommand,
        params: dict | None = None,
    ) -> dict:
        device_id = await self._resolve_device_id(external_id)
        body: dict[str, Any] = {"deviceId": device_id, "type": COMMAND_MAP[cmd]}
        if params:
            body.update(params)
        resp = await self._request("POST", "/api/commands/send", json=body)
        if resp.is_success:
            result = resp.json() if resp.content else None
            return {"status": "sent", "result": result}
        return {"status": "failed", "result": resp.text}

    # --- состояние / поллинг ---------------------------------------------------

    async def get_state(self, external_id: str) -> NormalizedPoint | None:
        device_id = await self._resolve_device_id(external_id)
        resp = await self._request("GET", "/api/positions", params={"deviceId": device_id})
        resp.raise_for_status()
        positions: list[dict] = resp.json()
        if not positions:
            return None
        return self._normalize(positions[0], external_id=external_id)

    async def _fetch_all_positions(self) -> list[dict]:
        resp = await self._request("GET", "/api/positions")
        resp.raise_for_status()
        return resp.json()

    # --- нормализация ------------------------------------------------------------

    def _normalize(self, position: dict, external_id: str | None = None) -> NormalizedPoint:
        attrs = dict(position.get("attributes") or {})
        device_id = position.get("deviceId")
        ext_id = external_id or self._unique_by_device.get(device_id) or str(device_id)

        status_int = _parse_status(attrs.pop("status", None))
        status_raw = hex(status_int) if status_int is not None else None
        # Бит 27: 0 = заблокирован. plan/09: подтверждено окном engineStop/engineResume на 139.
        engine_blocked = None if status_int is None else not bool((status_int >> 27) & 1)

        total_distance_m = attrs.pop("totalDistance", None)
        total_distance_km = (
            Decimal(str(total_distance_m)) / Decimal(1000) if total_distance_m is not None else None
        )

        return NormalizedPoint(
            external_id=ext_id,
            ts=_parse_dt(position.get("deviceTime")),
            server_ts=datetime.now(timezone.utc),
            lat=position.get("latitude"),
            lon=position.get("longitude"),
            speed_knots=position.get("speed"),
            course=position.get("course"),
            altitude=position.get("altitude"),
            valid=bool(position.get("valid", False)),
            ignition=attrs.pop("ignition", None),
            motion=attrs.pop("motion", None),
            total_distance_km=total_distance_km,
            engine_blocked=engine_blocked,
            status_raw=status_raw,
            attributes=attrs,
        )

    # --- живой поток ------------------------------------------------------------

    async def stream(self) -> AsyncIterator[NormalizedPoint]:
        if not self._ws_enabled:
            async for point in self._poll_stream():
                yield point
            return
        async for point in self._ws_stream():
            yield point

    async def _poll_stream(self) -> AsyncIterator[NormalizedPoint]:
        while True:
            try:
                for position in await self._fetch_all_positions():
                    yield self._normalize(position)
            except Exception:
                logger.warning("traccar polling failed", exc_info=True)
            await asyncio.sleep(self._poll_interval_seconds)

    async def _ws_stream(self) -> AsyncIterator[NormalizedPoint]:
        backoff = _WS_BACKOFF_INITIAL
        while True:
            try:
                if not self._authenticated:
                    await self._authenticate()
                headers = {"Cookie": self._cookie_header()}
                async with self._ws_connect(self._ws_url(), additional_headers=headers) as ws:
                    backoff = _WS_BACKOFF_INITIAL  # соединение установлено — сбрасываем бэкофф
                    self.connected = True
                    async for raw in ws:
                        for position in _extract_positions(raw):
                            yield self._normalize(position)
                # Штатное закрытие сервером исключения не даёт: без этого блока
                # connected остался бы True навсегда, а реконнект крутился бы
                # без паузы.
                logger.info("traccar websocket closed by server, reconnecting")
                self.connected = False
                self._authenticated = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _WS_BACKOFF_MAX)
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception:
                logger.warning("traccar websocket disconnected, reconnecting", exc_info=True)
                self.connected = False
                self._authenticated = False
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _WS_BACKOFF_MAX)


def _extract_positions(raw: str | bytes) -> list[dict]:
    frame = json.loads(raw)
    return frame.get("positions") or []


def _parse_status(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
