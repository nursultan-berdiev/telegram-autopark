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
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.models import CommandType
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
BLOCK_HISTORY_LIMIT = 20

# Человекочитаемое состояние блока по статусу команды.
_BLOCK_STATE = {
    "armed": "взведён (сработает на остановке)",
    "queued": "в полёте",
    "sent": "в полёте",
    "acked": "заглушён",
    "unconfirmed": "заглушён (не подтверждено телеметрией)",
}

# Источник команды → почему (значения CommandSource). photo — задел под блок за
# пропуск фотоконтроля; держим в таблице, чтобы история не падала на сырой код.
_SOURCE_LABEL = {
    "manual": "вручную",
    "overdue": "за аренду",
    "fines": "за штрафы",
    "photo": "за фотоконтроль",
}

# Статус команды двигателя — человекочитаемо (для истории блокировок). `failed`
# ставится не только при сбое отправки, но и когда блок снят разблокировкой или
# админ отменил взвод — поэтому нейтральное «не выполнена», а подробности даёт
# поле result (см. block_history).
_CMD_STATUS_LABEL = {
    "queued": "в полёте",
    "sent": "в полёте",
    "armed": "взведено (сработает на остановке)",
    "acked": "исполнено",
    "unconfirmed": "исполнено (не подтверждено телеметрией)",
    "failed": "не выполнена",
    "blocked_by_safety": "отклонено защитой (машина не стояла)",
}


def _num(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def _local(dt: datetime | None) -> str | None:
    """Абсолютное время — готовой строкой в часовом поясе парка «ГГГГ-ММ-ДД ЧЧ:ММ».

    Отдаём уже местное время, чтобы ассистент его НЕ пересчитывал из UTC сам: на
    этом он недетерминирован (один и тот же момент показывал то верно, то со
    сдвигом). Модели в промпте сказано показывать время как есть.
    """
    if dt is None:
        return None
    # Наивное время считаем UTC (в БД хранится UTC; SQLite возвращает без tz).
    # Иначе astimezone принял бы его за системное локальное и сдвинул неверно.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")


def _normalize_plate(value: str) -> str:
    """Номер к сравнимому виду: верхний регистр, только буквы/цифры (без пробелов/дефисов)."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


async def _resolve_plate(session, plate: str) -> dict:
    """Терпимый поиск машины по номеру — для read-only инструментов ассистента.

    Возврат (JSON-дружелюбный):
      {"car_id": int, "plate": str}            — найдено однозначно;
      {"error": str, "candidates": [...]}      — подходит несколько, нужна точность;
      {"error": str, "available": [...]}       — не найдено (даю список номеров парка).

    Общий `cars.find_id_by_plate` (им шлёт команды бот) намеренно не трогаем: там
    нужна строгая точность, иначе можно заблокировать не ту машину.
    """
    pm = await cars_domain.plate_map(session)  # {car_id: plate}
    for cid, pl in pm.items():  # точное совпадение как есть
        if pl == plate:
            return {"car_id": cid, "plate": pl}
    q = _normalize_plate(plate)
    if not q:
        return {"error": "не указан номер машины", "available": sorted(pm.values())}
    norm = {cid: _normalize_plate(pl) for cid, pl in pm.items()}
    exact = [cid for cid, n in norm.items() if n == q]
    if len(exact) == 1:
        return {"car_id": exact[0], "plate": pm[exact[0]]}
    # `n` обязателен непустой: иначе номер из одних спецсимволов (norm == "")
    # совпал бы с любым запросом ("" in q == True) и ломал поиск.
    subs = [cid for cid, n in norm.items() if n and (q in n or n in q)]
    if len(subs) == 1:
        return {"car_id": subs[0], "plate": pm[subs[0]]}
    if len(subs) > 1:
        return {
            "error": f"по «{plate}» подходит несколько машин — уточните номер",
            "candidates": sorted(pm[c] for c in subs),
        }
    return {
        "error": f"машина с номером «{plate}» не найдена",
        "available": sorted(pm.values()),
    }


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
            "next_due": _local(it.next_due),
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
        {"plate": plate, "driver": name, "amount": _num(p.amount), "at": _local(p.created_at)}
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
    res = await _resolve_plate(session, plate)
    if "car_id" not in res:
        return res
    car_id, plate = res["car_id"], res["plate"]
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
        res = await _resolve_plate(session, plate)
        if "car_id" not in res:
            return [res]  # ошибка/варианты одним элементом — список сохраняем
        car_id = res["car_id"]
    rows = await fines_domain.list_fleet_fines(session, car_id=car_id, only_unpaid=True)
    return [
        {
            "plate": pl,
            "article": f.article,
            "violation": f.violation_title,
            "amount": _num(f.amount),
            "amount_to_pay": _num(f.amount_to_pay),
            "issued_at": _local(f.issued_at),
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
            "at": _local(a.triggered_at),
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


async def block_history(session, plate: str) -> dict:
    """История блокировок двигателя машины: когда, за что и чем снято, плюс «сейчас».

    Берём команды двигателя (блок/разблок) этой машины от новых к старым. По каждой:
    когда, действие (блокировка/разблокировка), причина (вручную/за аренду/за штрафы),
    статус (исполнено/не удалось/…), кто (id админа — только для ручных), когда
    подтверждено. `currently_blocked` — текущее состояние по телеметрии.
    """
    res = await _resolve_plate(session, plate)
    if "car_id" not in res:
        return res
    car_id, plate = res["car_id"], res["plate"]

    events: list[dict] = []
    for cm in await commands_domain.list_commands(session, car_id):
        if cm.type not in (CommandType.engine_stop, CommandType.engine_resume):
            continue
        events.append(
            {
                "at": _local(cm.created_at),
                "action": (
                    "блокировка" if cm.type == CommandType.engine_stop else "разблокировка"
                ),
                "reason": _SOURCE_LABEL.get(cm.source, cm.source),
                "status": _CMD_STATUS_LABEL.get(cm.status.value, cm.status.value),
                # Подробность статуса (почему «не выполнена»: снята разблокировкой,
                # отменён взвод, сбой отправки и т.п.) — есть не всегда.
                "detail": cm.result or None,
                # Кто снял/поставил вручную: id админа. Для авто (аренда/штрафы) — None.
                "by_admin_id": cm.requested_by if cm.source == "manual" else None,
                "acked_at": _local(cm.acked_at),
            }
        )
        if len(events) >= BLOCK_HISTORY_LIMIT:
            break

    st = await telemetry_domain.get_state(session, car_id)
    currently_blocked = bool(st.engine_blocked) if st is not None else None
    return {"plate": plate, "currently_blocked": currently_blocked, "events": events}
