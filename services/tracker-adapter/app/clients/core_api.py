"""Клиент к core-api для пуша батчей телеметрии (INGEST_TOKEN — не общий CORE_API_TOKEN)."""
from __future__ import annotations

from dataclasses import asdict

import httpx

from app.providers.base import NormalizedPoint


class CoreApiClient:
    def __init__(
        self,
        base_url: str,
        ingest_token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = ingest_token
        self._client = http_client or httpx.AsyncClient(timeout=15.0)

    async def push_telemetry_batch(self, points: list[NormalizedPoint]) -> httpx.Response:
        payload = [_serialize_point(p) for p in points]
        resp = await self._client.post(
            f"{self._base_url}/telemetry/batch",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp

    async def aclose(self) -> None:
        await self._client.aclose()


def _serialize_point(point: NormalizedPoint) -> dict:
    data = asdict(point)
    data["ts"] = point.ts.isoformat()
    data["server_ts"] = point.server_ts.isoformat()
    if point.total_distance_km is not None:
        data["total_distance_km"] = str(point.total_distance_km)
    return data
