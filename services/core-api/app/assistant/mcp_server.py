"""MCP-сервер автопарка для ИИ-ассистента (read-only).

Клод (через claude-gateway CLI) сам вызывает нужный инструмент под вопрос, вместо
чтения статического снимка. Смонтирован в FastAPI по пути `/mcp` (см. `app/main.py`).
Инструменты — тонкие обёртки над `app.assistant.tools`, каждая открывает свою сессию.
Только ЧТЕНИЕ: команд/блокировок здесь нет.

Версия mcp закреплена (1.30.x) ради совместимости со starlette<0.42 и pydantic 2.x,
которыми пользуется core-api; streamable-HTTP, stateless. Инструменты возвращают JSON
СТРОКОЙ (а не dict) намеренно: так FastMCP не генерирует output-схему под конкретную
версию pydantic — для разных окружений это ломалось.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import TransportSecuritySettings

from app.assistant import tools
from app.config import settings
from app.db.base import async_session_maker


def _allowed_hosts() -> list[str]:
    """Host, по которому gateway зовёт наш /mcp, должен пройти DNS-rebinding защиту
    FastMCP (по умолчанию только localhost) — иначе всё молча уходит в фолбэк."""
    host = urlparse(settings.mcp_url).netloc or "fleet_core_api:8000"
    bare = host.split(":")[0]
    return sorted({host, bare, "localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000"})


mcp = FastMCP(
    "fleet",
    stateless_http=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        allowed_hosts=_allowed_hosts(), allowed_origins=["*"]
    ),
)


def _json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@mcp.tool(structured_output=False)
async def fleet_overview() -> str:
    """Обзор парка: машины, кто за рулём, свободно/занято, кто на связи."""
    async with async_session_maker() as session:
        return _json(await tools.fleet_overview(session))


@mcp.tool(structured_output=False)
async def payments_status() -> str:
    """Графики аренды: по каждому водителю срок, долг и просрочка."""
    async with async_session_maker() as session:
        return _json(await tools.payments_status(session))


@mcp.tool(structured_output=False)
async def recent_payments(hours: int = 24) -> str:
    """Оплаты аренды за последние `hours` часов: кто, сколько, когда."""
    async with async_session_maker() as session:
        return _json(await tools.recent_payments(session, hours=hours))


@mcp.tool(structured_output=False)
async def car_state(plate: str) -> str:
    """Состояние машины по номеру: онлайн, где, едет/стоит, зажигание, блок двигателя."""
    async with async_session_maker() as session:
        return _json(await tools.car_state(session, plate))


@mcp.tool(structured_output=False)
async def fines(plate: str | None = None) -> str:
    """Неоплаченные штрафы: по всему парку или по машине (укажи номер)."""
    async with async_session_maker() as session:
        return _json(await tools.fines(session, plate))


@mcp.tool(structured_output=False)
async def open_alerts() -> str:
    """Открытые тревоги по парку (просрочка, штрафы, блокировки, ТО)."""
    async with async_session_maker() as session:
        return _json(await tools.open_alerts(session))


@mcp.tool(structured_output=False)
async def blocked_cars() -> str:
    """Машины под блокировкой двигателя (заглушённые/взведённые) и причина."""
    async with async_session_maker() as session:
        return _json(await tools.blocked_cars(session))


# ASGI-приложение streamable-HTTP (монтируется в app/main.py).
mcp_app = mcp.streamable_http_app()
