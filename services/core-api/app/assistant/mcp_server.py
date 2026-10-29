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
    """Обзор всего парка: список машин и общая занятость. Начни с него, если
    нужно узнать номера машин или сопоставить неполный номер с полным.

    Параметры: нет.
    Возвращает: {total, free, occupied, cars:[{plate, model, driver, free, online}]},
    где plate — полный госномер (напр. 01KG139API), driver — ФИО или null (свободна),
    online — на связи ли трекер сейчас.
    Ограничения: только текущий срез; долги, штрафы, блокировки — в других инструментах."""
    async with async_session_maker() as session:
        return _json(await tools.fleet_overview(session))


@mcp.tool(structured_output=False)
async def payments_status() -> str:
    """Графики аренды по всем водителям: срок следующего платежа, долг и просрочка.

    Параметры: нет.
    Возвращает: список [{driver, plate, amount (размер платежа), next_due (ISO),
    debt_now (текущий долг), overdue_days, is_overdue, summary}].
    Ограничения: по всему парку сразу (фильтра по машине нет); это аренда, не штрафы."""
    async with async_session_maker() as session:
        return _json(await tools.payments_status(session))


@mcp.tool(structured_output=False)
async def recent_payments(hours: int = 24) -> str:
    """Поступившие оплаты аренды за последние `hours` часов: кто, сколько, когда.

    Параметры: hours (int, по умолчанию 24; обрезается в диапазон 1..720 часов).
    Возвращает: {window_hours, count, truncated (true — упёрлись в лимит 200),
    payments:[{plate, driver, amount, at (ISO)}]}.
    Ограничения: только оплаты аренды в окне; штрафы и графики — в других инструментах."""
    async with async_session_maker() as session:
        return _json(await tools.recent_payments(session, hours=hours))


@mcp.tool(structured_output=False)
async def car_state(plate: str) -> str:
    """Текущее состояние одной машины по номеру: где и что с ней прямо сейчас.

    Параметры: plate (строка). Можно неполный/неточный номер — сопоставление
    терпимо к регистру, пробелам и дефисам; по части номера найдёт, если она
    однозначна.
    Возвращает: {plate, online, last_seen_sec_ago, moving, speed_knots, ignition
    (зажигание), lat, lon, engine_blocked (заглушён ли двигатель сейчас)}.
    Если номер не сопоставлен — {error, candidates|available} (список номеров парка).
    Ограничения: только «сейчас», без истории; историю блокировок см. block_history."""
    async with async_session_maker() as session:
        return _json(await tools.car_state(session, plate))


@mcp.tool(structured_output=False)
async def fines(plate: str | None = None) -> str:
    """Неоплаченные штрафы: по всему парку или по одной машине.

    Параметры: plate (строка или не указан). Без plate — по всему парку. С plate —
    по машине; номер можно неполный/неточный (сопоставление терпимо к формату).
    Возвращает: список [{plate, article (статья), violation (нарушение), amount
    (полная сумма), amount_to_pay (к оплате, может быть со скидкой), issued_at (ISO)}].
    Если номер не сопоставлен — один элемент {error, candidates|available}.
    Ограничения: только НЕоплаченные; оплаченные не показывает."""
    async with async_session_maker() as session:
        return _json(await tools.fines(session, plate))


@mcp.tool(structured_output=False)
async def open_alerts() -> str:
    """Открытые (непогашенные) тревоги по парку: просрочка, новые штрафы, блокировки, ТО.

    Параметры: нет.
    Возвращает: список [{plate, type (тип тревоги), severity, text, at (ISO)}].
    Ограничения: только открытые тревоги; закрытые/историю не показывает."""
    async with async_session_maker() as session:
        return _json(await tools.open_alerts(session))


@mcp.tool(structured_output=False)
async def blocked_cars() -> str:
    """Машины, у которых двигатель заблокирован ПРЯМО СЕЙЧАС, и причина.

    Параметры: нет.
    Возвращает: список [{plate, state (заглушён/взведён/…), source (вручную/за
    аренду/за штрафы/None)}]. Пустой список — сейчас никто не заблокирован.
    Ограничения: только текущие блокировки. Был ли заблокирован раньше, когда и чем
    снято — смотри block_history по конкретной машине."""
    async with async_session_maker() as session:
        return _json(await tools.blocked_cars(session))


@mcp.tool(structured_output=False)
async def block_history(plate: str) -> str:
    """История блокировок/разблокировок двигателя одной машины (от новых к старым).

    Отвечает на «был ли заблокирован раньше, когда, за что и кто снял».
    Параметры: plate (строка; можно неполный/неточный номер).
    Возвращает: {plate, currently_blocked (заблокирован ли сейчас),
    events:[{at (ISO), action (блокировка/разблокировка), reason (вручную/за
    аренду/за штрафы/за фотоконтроль), status (исполнено/не выполнена/взведено/…),
    detail (почему не выполнена: снята разблокировкой, отменён взвод, сбой — или
    null), by_admin_id (кто — только для ручных, иначе null), acked_at (ISO|null)}]}.
    Если номер не сопоставлен — {error, candidates|available}.
    Ограничения: до 20 последних событий по этой машине; только двигатель."""
    async with async_session_maker() as session:
        return _json(await tools.block_history(session, plate))


# ASGI-приложение streamable-HTTP (монтируется в app/main.py).
mcp_app = mcp.streamable_http_app()
