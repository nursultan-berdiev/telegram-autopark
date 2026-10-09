"""Инструменты ИИ-ассистента автопарка: read-only запросы к домену.

Чистые функции (сессия → JSON-сериализуемый результат) — тестируются без MCP;
MCP-обёртки живут в `mcp_server.py`. Только ЧТЕНИЕ: ни команд, ни блокировок через
ассистента. Машины ИИ называет номером → внутри резолвим в car_id. Запросы берём
из `app.domain.*`, сырых select здесь нет.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain import alerts as alerts_domain
from app.domain import cars as cars_domain
from app.domain import commands as commands_domain
from app.domain import fines as fines_domain
from app.domain import payments as payments_domain
from app.domain import reports as reports_domain
from app.domain import telemetry as telemetry_domain

# Окно recent_payments и предел выдачи — защита от мусорных `hours` от LLM.
RECENT_HOURS_MAX = 720  # 30 суток
RECENT_LIMIT = 200

# Человекочитаемое состояние блока по статусу команды.
_BLOCK_STATE = {
    "armed": "взведён (сработает на остановке)",
    "queued": "в полёте",
    "sent": "в полёте",
    "acked": "заглушён",
    "unconfirmed": "заглушён (не подтверждено телеметрией)",
}


def _num(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def fleet_overview(session) -> dict:
    """Машины парка, кто за рулём, свободно/занято и кто сейчас на связи."""
    cars = await reports_domain.cars_with_drivers(session)
    states = await telemetry_domain.list_states(session)
    now = datetime.now(timezone.utc)
    items = [
        {
            "plate": c.plate,
            "model": c.model,
            "driver": c.driver.full_name if c.driver else None,
            "free": c.driver is None,
            "online": telemetry_domain.is_online(states.get(c.id), now),
        }
        for c in cars
    ]
    free = sum(1 for c in cars if c.driver is None)
    return {"total": len(cars), "free": free, "occupied": len(cars) - free, "cars": items}


async def payments_status(session) -> list[dict]:
    """Графики аренды: по каждому водителю срок, долг и просрочка."""
    items = await reports_domain.upcoming_payments(session)
    return [
        {
            "driver": it.name,
            "plate": it.car_plate,
            "amount": _num(it.amount),
            "next_due": _iso(it.next_due),
            "debt_now": _num(it.debt_now),
            "overdue_days": it.overdue_days,
            "is_overdue": it.is_overdue,
            "summary": it.summary,
        }
        for it in items
    ]


async def recent_payments(session, hours: int = 24) -> dict:
    """Оплаты аренды по парку за последние `hours` часов (кто, сколько, когда)."""
    try:
        window = max(1, min(int(hours), RECENT_HOURS_MAX))
    except (TypeError, ValueError):
        window = 24
    rows = await payments_domain.list_recent(session, hours=window, limit=RECENT_LIMIT)
    payments = [
        {"plate": plate, "driver": name, "amount": _num(p.amount), "at": _iso(p.created_at)}
        for p, plate, name in rows
    ]
    return {
        "window_hours": window,
        "count": len(payments),
        "truncated": len(payments) >= RECENT_LIMIT,
        "payments": payments,
    }


async def car_state(session, plate: str) -> dict:
    """Где машина и её состояние: онлайн, координаты, едет/стоит, зажигание, блок."""
    car_id = await cars_domain.find_id_by_plate(session, plate)
    if car_id is None:
        return {"error": f"машина с номером {plate} не найдена"}
    st = await telemetry_domain.get_state(session, car_id)
    if st is None:
        return {"plate": plate, "online": False, "note": "нет телеметрии"}
    now = datetime.now(timezone.utc)
    return {
        "plate": plate,
        "online": telemetry_domain.is_online(st, now),
        "last_seen_sec_ago": telemetry_domain.point_age_seconds(st, now),
        "moving": bool(st.motion) if st.motion is not None else None,
        "speed_knots": float(st.speed_knots) if st.speed_knots is not None else None,
        "ignition": st.ignition,
        "lat": st.lat,
        "lon": st.lon,
        "engine_blocked": st.engine_blocked,
    }


async def fines(session, plate: str | None = None) -> list[dict]:
    """Неоплаченные штрафы: по всему парку или по конкретной машине (по номеру)."""
    car_id = None
    if plate is not None:
        car_id = await cars_domain.find_id_by_plate(session, plate)
        if car_id is None:
            return [{"error": f"машина с номером {plate} не найдена"}]
    rows = await fines_domain.list_fleet_fines(session, car_id=car_id, only_unpaid=True)
    return [
        {
            "plate": pl,
            "article": f.article,
            "violation": f.violation_title,
            "amount": _num(f.amount),
            "amount_to_pay": _num(f.amount_to_pay),
            "issued_at": _iso(f.issued_at),
        }
        for f, pl in rows
    ]


async def open_alerts(session) -> list[dict]:
    """Открытые тревоги по парку (просрочка, штрафы, блокировки, ТО и т.п.)."""
    alerts = await alerts_domain.list_alerts(session, status="open")
    plates = await cars_domain.plate_map(session)
    return [
        {
            "plate": plates.get(a.car_id),
            "type": a.type.value,
            "severity": a.severity,
            "text": (a.payload or {}).get("text"),
            "at": _iso(a.triggered_at),
        }
        for a in alerts
    ]


async def blocked_cars(session) -> list[dict]:
    """Машины под блокировкой двигателя и причина (manual — вручную, overdue — за
    аренду, fines — за штрафы). Действующий блок = последняя значимая команда машины;
    плюс фактически заглушённые по телеметрии (`engine_blocked`)."""
    plates = await cars_domain.plate_map(session)
    out: list[dict] = []
    seen: set[int] = set()
    for cmd in await commands_domain.cars_under_block(session):
        seen.add(cmd.car_id)
        out.append(
            {
                "plate": plates.get(cmd.car_id),
                "state": _BLOCK_STATE.get(cmd.status.value, cmd.status.value),
                "source": cmd.source,
            }
        )
    for car_id, st in (await telemetry_domain.list_states(session)).items():
        if st.engine_blocked and car_id not in seen:
            out.append({"plate": plates.get(car_id), "state": "заглушён", "source": None})
    return out
