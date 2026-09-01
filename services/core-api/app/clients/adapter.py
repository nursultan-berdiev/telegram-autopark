"""HTTP-клиент к tracker-adapter: команды на устройство."""
from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class AdapterError(Exception):
    pass


async def send_command(external_id: str, command: str, params: dict | None = None) -> dict:
    """Возвращает {status, result} от адаптера; сеть — не повод терять аудит."""
    if not settings.adapter_url:
        raise AdapterError("ADAPTER_URL не задан")
    url = f"{settings.adapter_url.rstrip('/')}/devices/{external_id}/commands"
    headers = {"Authorization": f"Bearer {settings.adapter_token}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url, json={"type": command, "params": params}, headers=headers
            )
    except httpx.HTTPError as exc:
        raise AdapterError(f"адаптер недоступен: {exc}") from exc

    if response.status_code >= 400:
        raise AdapterError(f"адаптер вернул {response.status_code}: {response.text[:200]}")
    return response.json()
