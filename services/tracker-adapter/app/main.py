"""FastAPI-приложение tracker-adapter: ингест телеметрии + приём команд от core-api.

Своей БД нет — stateless-транслятор к Traccar. Защитного гейта на команды здесь
нет намеренно: это доменное решение core-api (см. plan/04), адаптер просто исполняет.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from app.clients.core_api import CoreApiClient
from app.config import settings
from app.ingest import IngestWorker
from app.providers.base import NormalizedPoint, TrackerCommand, TrackerProvider
from app.providers.registry import get_provider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    provider = get_provider("traccar")
    core_api = CoreApiClient(settings.core_api_url, settings.ingest_token)
    worker = IngestWorker(
        provider,
        core_api,
        batch_size=settings.telemetry_batch_size,
        flush_seconds=settings.telemetry_flush_seconds,
    )
    await worker.start()
    app.state.provider = provider
    app.state.core_api = core_api
    app.state.worker = worker
    try:
        yield
    finally:
        await worker.stop()
        await core_api.aclose()


app = FastAPI(title="tracker-adapter", lifespan=lifespan)


def get_provider_dep(request: Request) -> TrackerProvider:
    return request.app.state.provider


def get_worker_dep(request: Request) -> IngestWorker | None:
    return getattr(request.app.state, "worker", None)


async def require_adapter_token(request: Request) -> None:
    expected = f"Bearer {settings.adapter_token}"
    if request.headers.get("authorization") != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


class CommandRequest(BaseModel):
    type: TrackerCommand
    params: dict[str, Any] | None = None


class CommandResponse(BaseModel):
    status: str
    result: Any = None


def _serialize_point(point: NormalizedPoint) -> dict:
    data = asdict(point)
    data["ts"] = point.ts.isoformat()
    data["server_ts"] = point.server_ts.isoformat()
    if point.total_distance_km is not None:
        data["total_distance_km"] = str(point.total_distance_km)
    return data


@app.post(
    "/devices/{external_id}/commands",
    response_model=CommandResponse,
    dependencies=[Depends(require_adapter_token)],
)
async def send_command(
    external_id: str,
    body: CommandRequest,
    provider: TrackerProvider = Depends(get_provider_dep),
) -> CommandResponse:
    """Отказ описываем в теле ответа: 500-ка утекала админу в чат обрывком текста."""
    try:
        result = await provider.send_command(external_id, body.type, body.params)
    except ValueError as exc:
        logger.warning("устройство %s не найдено в Traccar: %s", external_id, exc)
        return CommandResponse(status="failed", result=f"устройство {external_id} не найдено")
    except Exception:  # noqa: BLE001 — причина уже в логе, наружу отдаём понятный текст
        logger.warning("команда %s на %s не прошла", body.type, external_id, exc_info=True)
        return CommandResponse(status="failed", result="трекинг-платформа недоступна")
    return CommandResponse(**result)


@app.get(
    "/devices/{external_id}/state",
    dependencies=[Depends(require_adapter_token)],
)
async def get_state(
    external_id: str,
    provider: TrackerProvider = Depends(get_provider_dep),
) -> dict | None:
    try:
        point = await provider.get_state(external_id)
    except Exception:  # noqa: BLE001 — состояние читаем как «нет данных», а не 500
        logger.warning("состояние %s недоступно", external_id, exc_info=True)
        return None
    return _serialize_point(point) if point is not None else None


@app.get("/health")
async def health(
    request: Request,
    worker: IngestWorker | None = Depends(get_worker_dep),
) -> dict:
    provider = getattr(request.app.state, "provider", None)
    return {
        "ok": True,
        "traccar": getattr(provider, "connected", None),
        "ingest": worker.is_running if worker is not None else False,
    }
