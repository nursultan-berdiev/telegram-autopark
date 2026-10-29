"""Инструменты ИИ-ассистента (read-only) и переключение gateway на tool-use."""
from datetime import datetime, timezone

from sqlalchemy import select

from app.assistant import tools
from app.clients import ai_gateway
from app.db.models import (
    AlertType,
    Car,
    CarState,
    Command,
    CommandStatus,
    CommandType,
    Document,
    Driver,
    Fine,
    FineStatus,
    PhotoInspection,
    SchedulePeriod,
    Tracker,
    TrackerProvider,
)
from app.domain import alerts as alerts_domain
from app.domain import payments as pay
from app.domain import schedules as sched
from app.clients.ai_gateway import RecognizedReceipt

NOW = datetime.now(timezone.utc)


async def _car(session, *, plate, model="Sonata", online=True, engine_blocked=False, driver_name=None):
    car = Car(plate=plate, model=model)
    session.add(car)
    await session.flush()
    if driver_name:
        session.add(Driver(tg_user_id=hash(plate) % 10**9, full_name=driver_name, phone="+1", inn=plate, car_id=car.id, active=True))
    tracker = Tracker(car_id=car.id, provider=TrackerProvider.traccar, external_id="t" + plate)
    session.add(tracker)
    await session.flush()
    session.add(CarState(
        car_id=car.id, tracker_id=tracker.id,
        last_ts=NOW if online else NOW.replace(year=2020),
        speed_knots=0.0, ignition=False, motion=False, engine_blocked=engine_blocked,
    ))
    await session.commit()
    return car


async def test_fleet_overview(session):
    await _car(session, plate="01KG195API", driver_name="Иванов", online=True)
    await _car(session, plate="01KG999API", driver_name=None, online=False)

    ov = await tools.fleet_overview(session)
    assert ov["total"] == 2 and ov["free"] == 1 and ov["occupied"] == 1
    by_plate = {c["plate"]: c for c in ov["cars"]}
    assert by_plate["01KG195API"]["driver"] == "Иванов" and by_plate["01KG195API"]["online"] is True
    assert by_plate["01KG999API"]["free"] is True and by_plate["01KG999API"]["online"] is False


async def test_payments_status(session):
    car = await _car(session, plate="01KG195API", driver_name="Иванов")
    driver = await session.scalar(select(Driver).where(Driver.car_id == car.id))
    await sched.set_schedule(
        session, driver_id=driver.id, period=SchedulePeriod.daily, interval_days=None,
        amount=1800.0, next_due_date=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    rows = await tools.payments_status(session)
    assert len(rows) == 1 and rows[0]["plate"] == "01KG195API"
    assert rows[0]["amount"] == 1800.0 and "summary" in rows[0]


async def test_recent_payments_window(session):
    car = await _car(session, plate="01KG195API", driver_name="Иванов")
    driver = await session.scalar(select(Driver).where(Driver.car_id == car.id))
    rec = RecognizedReceipt(True, 1800.0, "KGS", None, None, None)
    await pay.create_payment(
        session, driver_id=driver.id, car_id=car.id, amount=1800.0, paid_at=None,
        receipt_file_id=None, receipt_path=None, receipt_hash="h1", recognized=rec,
    )
    await session.commit()
    recent = await tools.recent_payments(session, hours=24)
    assert recent["count"] == 1 and recent["truncated"] is False
    assert recent["payments"][0]["plate"] == "01KG195API" and recent["payments"][0]["amount"] == 1800.0
    # hours от LLM валидируется: огромное → клампится, не OverflowError.
    assert (await tools.recent_payments(session, hours=10**9))["window_hours"] == 720
    assert (await tools.recent_payments(session, hours=-5))["window_hours"] == 1


async def test_car_state_and_unknown_plate(session):
    await _car(session, plate="01KG195API", engine_blocked=True)
    st = await tools.car_state(session, "01KG195API")
    assert st["plate"] == "01KG195API" and st["online"] is True and st["engine_blocked"] is True
    assert "error" in await tools.car_state(session, "01KG000XXX")


async def test_fines(session):
    car = await _car(session, plate="01KG195API")
    session.add(Fine(car_id=car.id, issued_at=NOW, status=FineStatus.unpaid, external_ref="r1", article="ст.1", amount=1000.0))
    session.add(Fine(car_id=car.id, issued_at=NOW, status=FineStatus.paid, external_ref="r2", amount=500.0))
    await session.commit()
    rows = await tools.fines(session)
    assert len(rows) == 1 and rows[0]["plate"] == "01KG195API" and rows[0]["article"] == "ст.1"
    assert "error" in (await tools.fines(session, "01KG000XXX"))[0]


async def test_open_alerts(session):
    car = await _car(session, plate="01KG195API")
    await alerts_domain.raise_alert(session, car_id=car.id, atype=AlertType.overdue_payment, payload={}, text="просрочка")
    await session.commit()
    rows = await tools.open_alerts(session)
    assert len(rows) == 1 and rows[0]["plate"] == "01KG195API" and rows[0]["text"] == "просрочка"


async def test_blocked_cars(session):
    car1 = await _car(session, plate="01KG195API")  # взведён (armed)
    await _car(session, plate="01KG999API", engine_blocked=True)  # заглушён по телеметрии
    car3 = await _car(session, plate="01KG637APR")  # блок СНЯТ более поздним resume
    session.add(Command(car_id=car1.id, type=CommandType.engine_stop, status=CommandStatus.armed, source="overdue", created_at=NOW, updated_at=NOW))
    session.add(Command(car_id=car3.id, type=CommandType.engine_stop, status=CommandStatus.acked, source="overdue", created_at=NOW, updated_at=NOW))
    session.add(Command(car_id=car3.id, type=CommandType.engine_resume, status=CommandStatus.acked, source="manual", created_at=NOW, updated_at=NOW))
    await session.commit()
    by_plate = {r["plate"]: r for r in await tools.blocked_cars(session)}
    assert "взведён" in by_plate["01KG195API"]["state"] and by_plate["01KG195API"]["source"] == "overdue"
    assert by_plate["01KG999API"]["state"] == "заглушён"
    assert "01KG637APR" not in by_plate, "снятый resume блок не считается действующим"


# --- gateway: переключение на инструменты -----------------------------------


async def test_gateway_tools_payload(monkeypatch):
    """В режиме инструментов шлём mcp_servers+system_prompt (без снимка)."""
    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", True)
    monkeypatch.setattr(ai_gateway.settings, "mcp_url", "http://core/mcp/")
    monkeypatch.setattr(ai_gateway.settings, "mcp_token", "secret")
    captured = {}

    async def _fake_post(path, payload, **kw):
        captured["path"], captured["payload"] = path, payload
        return {"text": "ответ"}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _fake_post)

    out = await ai_gateway._answer_owner_query_http("кто оплатил сегодня?", "СНИМОК-НЕ-ШЛЁМ")
    assert out == "ответ"
    p = captured["payload"]
    assert p["prompt"] == "кто оплатил сегодня?"
    assert "mcp_servers" in p and p["mcp_servers"]["mcpServers"]["fleet"]["url"] == "http://core/mcp/"
    assert p["mcp_servers"]["mcpServers"]["fleet"]["headers"]["Authorization"] == "Bearer secret"
    assert p["allowed_tools"] == ["mcp__fleet"]
    assert "СНИМОК-НЕ-ШЛЁМ" not in str(p), "снимок не должен уходить в промпт"


async def test_gateway_fallback_on_tools_exception(monkeypatch):
    """Сбой tool-режима → фолбэк на снимок (ветка, которую просил ревью)."""
    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", True)
    monkeypatch.setattr(ai_gateway.settings, "mcp_url", "http://core/mcp/")
    monkeypatch.setattr(ai_gateway.settings, "mcp_token", "secret")
    calls = []

    async def _post(path, payload, **kw):
        calls.append(payload)
        if "mcp_servers" in payload:
            raise RuntimeError("gateway/mcp недоступен")
        return {"text": "из снимка"}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _post)
    out = await ai_gateway._answer_owner_query_http("вопрос", "ДАННЫЕ-СНИМКА")
    assert out == "из снимка"
    assert len(calls) == 2 and "ДАННЫЕ-СНИМКА" in calls[1]["prompt"]


async def test_gateway_fallback_to_snapshot_when_disabled(monkeypatch):
    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", False)
    captured = {}

    async def _fake_post(path, payload, **kw):
        captured["payload"] = payload
        return {"text": "ответ"}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _fake_post)
    out = await ai_gateway._answer_owner_query_http("вопрос", "ДАННЫЕ-СНИМКА")
    assert out == "ответ"
    assert "mcp_servers" not in captured["payload"]
    assert "ДАННЫЕ-СНИМКА" in captured["payload"]["prompt"], "выключено → старый снимок-путь"


# --- новое: терпимый поиск по номеру, история блокировок, память разговора ----


async def test_car_state_partial_and_normalized_plate(session):
    await _car(session, plate="01KG195API", engine_blocked=True)
    # неполный номер
    st = await tools.car_state(session, "195")
    assert st.get("plate") == "01KG195API" and st.get("engine_blocked") is True
    # регистр и пробелы
    st2 = await tools.car_state(session, "01kg 195 api")
    assert st2.get("plate") == "01KG195API"


async def test_car_state_ambiguous_plate_lists_candidates(session):
    await _car(session, plate="01KG195API")
    await _car(session, plate="02KG195BPX")
    res = await tools.car_state(session, "195")
    assert "car_id" not in res and "error" in res
    assert set(res["candidates"]) == {"01KG195API", "02KG195BPX"}


async def test_car_state_unknown_plate_lists_available(session):
    await _car(session, plate="01KG195API")
    res = await tools.car_state(session, "777")
    assert "error" in res and res["available"] == ["01KG195API"]


async def test_fines_by_partial_plate(session):
    car = await _car(session, plate="01KG138API")
    session.add(Fine(car_id=car.id, issued_at=NOW, status=FineStatus.unpaid, external_ref="r1", article="ст.187", amount=1000.0))
    await session.commit()
    rows = await tools.fines(session, "138")  # неполный номер должен сработать
    assert len(rows) == 1 and rows[0]["plate"] == "01KG138API" and rows[0]["article"] == "ст.187"


async def test_block_history(session):
    car = await _car(session, plate="01KG138API", engine_blocked=False)
    # Хронология (id по возрастанию): блок за штрафы (исполнен), снят вручную
    # админом 123, затем блок за аренду «не выполнен» (снят разблокировкой).
    session.add(Command(car_id=car.id, type=CommandType.engine_stop, status=CommandStatus.acked, source="fines", created_at=NOW, updated_at=NOW, acked_at=NOW))
    await session.flush()
    session.add(Command(car_id=car.id, type=CommandType.engine_resume, status=CommandStatus.acked, source="manual", requested_by=123, created_at=NOW, updated_at=NOW, acked_at=NOW))
    await session.flush()
    session.add(Command(car_id=car.id, type=CommandType.engine_stop, status=CommandStatus.failed, source="overdue", result="снята разблокировкой двигателя", created_at=NOW, updated_at=NOW))
    await session.commit()

    hist = await tools.block_history(session, "138")  # и номер неполный
    assert hist["plate"] == "01KG138API" and hist["currently_blocked"] is False
    ev = hist["events"]
    assert len(ev) == 3
    # Новые сверху. `failed` → нейтральное «не выполнена», причина — в detail.
    assert ev[0]["action"] == "блокировка" and ev[0]["reason"] == "за аренду"
    assert ev[0]["status"] == "не выполнена" and ev[0]["detail"] == "снята разблокировкой двигателя"
    assert ev[1]["action"] == "разблокировка" and ev[1]["reason"] == "вручную" and ev[1]["by_admin_id"] == 123
    assert ev[2]["action"] == "блокировка" and ev[2]["reason"] == "за штрафы" and ev[2]["status"] == "исполнено"
    # Авто-команды без «кто».
    assert ev[0]["by_admin_id"] is None and ev[2]["by_admin_id"] is None


async def test_block_history_photo_source_labeled(session):
    car = await _car(session, plate="01KG195API", engine_blocked=False)
    session.add(Command(car_id=car.id, type=CommandType.engine_stop, status=CommandStatus.acked, source="photo", created_at=NOW, updated_at=NOW, acked_at=NOW))
    await session.commit()
    hist = await tools.block_history(session, "01KG195API")
    assert hist["events"][0]["reason"] == "за фотоконтроль"  # source=photo не «сырым»


async def test_resolve_plate_ignores_empty_normalized(session):
    # Номер из одних спецсимволов (норм. → "") не должен совпадать со всем подряд.
    await _car(session, plate="01KG138API")
    await _car(session, plate="—")
    st = await tools.car_state(session, "138")
    assert st.get("plate") == "01KG138API"  # нашёл 138, не запутался в пустом номере


async def test_tools_payload_carries_history_and_opus_model(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", True)
    monkeypatch.setattr(ai_gateway.settings, "mcp_url", "http://core/mcp/")
    monkeypatch.setattr(ai_gateway.settings, "mcp_token", "secret")
    monkeypatch.setattr(ai_gateway.settings, "assistant_model", "opus")
    captured = {}

    async def _fake_post(path, payload, **kw):
        captured["payload"] = payload
        return {"text": "ответ"}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _fake_post)
    history = [
        SimpleNamespace(role="user", text="Сколько машин?"),
        SimpleNamespace(role="assistant", text="Пять."),
    ]
    out = await ai_gateway._answer_owner_query_http("а свободных?", "СНИМОК", history)
    assert out == "ответ"
    p = captured["payload"]
    assert p["model"] == "opus", "ассистент ходит на выбранной модели (Opus)"
    assert "Сколько машин?" in p["prompt"] and "Пять." in p["prompt"]
    assert "Новый вопрос владельца: а свободных?" in p["prompt"]


async def test_compose_owner_prompt_without_history_is_bare_question():
    assert ai_gateway._compose_owner_prompt("вопрос", None) == "вопрос"
    assert ai_gateway._compose_owner_prompt("вопрос", []) == "вопрос"


# --- срез служебной мета-строки из ответа ассистента (детерминированно) -------


def test_strip_meta_preamble_removes_leading_english_meta():
    from app.clients import ai_gateway as gw
    # Реальные утечки Opus на вопрос «помнишь что я спрашивал»:
    a = "The вопрос владельца: помнит ли он что спрашивал ранее.\n\nДа, помню. Вы спрашивали про блок."
    assert gw._strip_meta_preamble(a) == "Да, помню. Вы спрашивали про блок."
    b = "The user asks what they asked earlier.\n\nДа, помню. Вы спрашивали про штрафы."
    assert gw._strip_meta_preamble(b) == "Да, помню. Вы спрашивали про штрафы."


def test_strip_meta_preamble_keeps_normal_answers():
    from app.clients import ai_gateway as gw
    ok = "Да, помню. Вы спрашивали про блок двигателя и штрафы."
    assert gw._strip_meta_preamble(ok) == ok
    # «the» в середине и латиница в номере не триггерят срез.
    multi = "Состояние 01KGTEST01:\n- трекер офлайн\n- двигатель не заблокирован"
    assert gw._strip_meta_preamble(multi) == multi


def test_strip_meta_preamble_does_not_empty_single_line():
    from app.clients import ai_gateway as gw
    only = "The user asks something"  # нет тела после строки — не режем в пустоту
    assert gw._strip_meta_preamble(only) == only


# --- время в ответах инструментов — в часовом поясе парка (не сырой UTC) ------
#
# Пояс фиксируем на Asia/Bishkek (+6) и сверяем с ЛИТЕРАЛОМ, а не с пересчётом
# тем же кодом: при TZ=UTC в окружении тавтологичная проверка прошла бы и со
# сломанным пересчётом (замечание ревью). UTC 05:26 → Бишкек 11:26.
import re as _re

from datetime import datetime as _dt

from app.config import settings as _settings

_TS_RE = _re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")  # «ГГГГ-ММ-ДД ЧЧ:ММ», не ISO


def test_local_helper_none_naive_aware(monkeypatch):
    from app.assistant import tools as t
    monkeypatch.setattr(_settings, "timezone", "Asia/Bishkek")
    assert t._local(None) is None
    # naive трактуется как UTC
    assert t._local(_dt(2026, 10, 27, 5, 26)) == "2026-10-27 11:26"
    # aware (UTC) — тот же момент
    assert t._local(_dt(2026, 10, 27, 5, 26, tzinfo=timezone.utc)) == "2026-10-27 11:26"
    # aware в другом поясе приводится к поясу парка
    from zoneinfo import ZoneInfo
    assert t._local(_dt(2026, 10, 27, 11, 26, tzinfo=ZoneInfo("Asia/Bishkek"))) == "2026-10-27 11:26"


async def test_block_history_times_are_park_local(session, monkeypatch):
    monkeypatch.setattr(_settings, "timezone", "Asia/Bishkek")
    car = await _car(session, plate="01KG138API", engine_blocked=False)
    t = _dt(2026, 10, 27, 5, 26, tzinfo=timezone.utc)   # UTC → Бишкек 11:26
    session.add(Command(car_id=car.id, type=CommandType.engine_stop, status=CommandStatus.acked,
                        source="fines", created_at=t, updated_at=t, acked_at=t))
    await session.commit()
    ev = (await tools.block_history(session, "138"))["events"][0]
    assert ev["at"] == "2026-10-27 11:26"
    assert ev["acked_at"] == "2026-10-27 11:26"
    assert "T" not in ev["at"] and "+" not in ev["at"]      # не ISO-UTC


async def test_fines_issued_at_is_park_local(session, monkeypatch):
    monkeypatch.setattr(_settings, "timezone", "Asia/Bishkek")
    car = await _car(session, plate="01KG195API")
    t = _dt(2026, 10, 27, 5, 26, tzinfo=timezone.utc)
    session.add(Fine(car_id=car.id, issued_at=t, status=FineStatus.unpaid, external_ref="r1",
                     article="Ст. 187", amount=1000.0, amount_to_pay=300.0))
    await session.commit()
    rows = await tools.fines(session, "195")
    assert rows[0]["issued_at"] == "2026-10-27 11:26"


async def test_other_time_fields_are_local_format(session):
    # next_due (payments_status), at (recent_payments), at (open_alerts) — тоже
    # местное время формата «ГГГГ-ММ-ДД ЧЧ:ММ», а не ISO (в PR не были покрыты).
    from app.db.models import AlertType, Driver, SchedulePeriod
    from app.domain import alerts as alerts_domain
    from app.domain import payments as pay
    from app.domain import schedules as sched
    car = await _car(session, plate="01KG195API", driver_name="Иванов")
    drv = await session.scalar(select(Driver).where(Driver.car_id == car.id))
    await sched.set_schedule(session, driver_id=drv.id, period=SchedulePeriod.daily,
                             interval_days=None, amount=1800.0,
                             next_due_date=_dt(2027, 1, 1, tzinfo=timezone.utc))
    await pay.create_payment(session, driver_id=drv.id, car_id=car.id, amount=1800.0, paid_at=None,
                             receipt_file_id=None, receipt_path=None, receipt_hash="h1",
                             recognized=RecognizedReceipt(True, 1800.0, "KGS", None, None, None))
    await alerts_domain.raise_alert(session, car_id=car.id, atype=AlertType.overdue_payment,
                                    payload={}, text="просрочка")
    await session.commit()
    assert _TS_RE.match((await tools.payments_status(session))[0]["next_due"])
    assert _TS_RE.match((await tools.recent_payments(session))["payments"][0]["at"])
    assert _TS_RE.match((await tools.open_alerts(session))[0]["at"])


# --- новые инструменты: фотоконтроль и документы машины -----------------------


async def test_car_photo_inspections(session):
    car = await _car(session, plate="01KG138API", driver_name="Асанов Азамат")
    drv = await session.scalar(select(Driver).where(Driver.car_id == car.id))
    session.add(PhotoInspection(
        driver_id=drv.id, car_id=car.id,
        front_file_id="F", rear_file_id="R", left_file_id="L", right_file_id=None,
        interior_front_file_id="IF", interior_rear_file_id="IR",
        has_issues=True, comment="царапина на двери", created_at=NOW,
    ))
    await session.commit()
    res = await tools.car_photo_inspections(session, "138")   # неполный номер
    assert res["plate"] == "01KG138API" and res["count"] == 1
    insp = res["inspections"][0]
    assert insp["driver"] == "Асанов Азамат"
    assert insp["has_issues"] is True and insp["comment"] == "царапина на двери"
    # Сданный ракурс → ссылка на медиа-прокси; не сданный (right=None) → null.
    assert insp["photos"]["front"] == f"/api/media/inspection/{insp['id']}?angle=front"
    assert insp["photos"]["right"] is None
    # Неизвестный номер — ошибка со списком доступных.
    miss = await tools.car_photo_inspections(session, "777")
    assert "error" in miss and "available" in miss


async def test_car_documents(session):
    car = await _car(session, plate="01KG195API")
    session.add(Document(car_id=car.id, title="ОСАГО", file_id="BQAC",
                         file_kind="document", file_name="osago.pdf", created_by=1, created_at=NOW))
    await session.commit()
    res = await tools.car_documents(session, "195")
    assert res["plate"] == "01KG195API" and res["count"] == 1 and "note" in res
    d = res["documents"][0]
    assert d["title"] == "ОСАГО" and d["file_kind"] == "document"
    assert d["url"] == f"/api/media/document/{d['id']}"
    assert d["added"] is not None
    # Неизвестный номер — ошибка.
    assert "error" in await tools.car_documents(session, "777")


# --- ответ из одной служебной англ. строки без ответа -> фолбэк ---------------

_META_ONLY = ("The user's question is just asking what they asked about earlier — "
              "I can answer directly from the conversation history. No tools needed.")


def _tools_on(monkeypatch):
    monkeypatch.setattr(ai_gateway.settings, "assistant_use_tools", True)
    monkeypatch.setattr(ai_gateway.settings, "mcp_url", "http://core/mcp/")
    monkeypatch.setattr(ai_gateway.settings, "mcp_token", "secret")


async def test_meta_only_tools_answer_falls_back_to_snapshot(monkeypatch):
    _tools_on(monkeypatch)
    calls = []

    async def _post(path, payload, **kw):
        calls.append(payload)
        if "mcp_servers" in payload:
            return {"text": _META_ONLY}          # реальная утечка с прода
        return {"text": "Да, помню: вы спрашивали про блокировки и штрафы."}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _post)
    out = await ai_gateway._answer_owner_query_http("Ты помнишь что я спрашивал?", "СНИМОК")
    assert out == "Да, помню: вы спрашивали про блокировки и штрафы."
    assert len(calls) == 2, "служебный ответ инструментов не показываем — идём в фолбэк"


async def test_russian_tools_answer_no_fallback(monkeypatch):
    _tools_on(monkeypatch)
    calls = []

    async def _post(path, payload, **kw):
        calls.append(payload)
        return {"text": "Сейчас ни одна машина не заблокирована."}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _post)
    out = await ai_gateway._answer_owner_query_http("кто заблокирован?", "СНИМОК")
    assert out == "Сейчас ни одна машина не заблокирована." and len(calls) == 1


async def test_both_meta_only_gives_safe_message(monkeypatch):
    _tools_on(monkeypatch)
    monkeypatch.setattr(ai_gateway.settings, "ai_backend", "http")

    async def _post(path, payload, **kw):
        return {"text": _META_ONLY}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _post)
    out = await ai_gateway.answer_owner_query("?", "СНИМОК")   # через диспетчер
    assert out == ai_gateway._NO_ANSWER and "The user" not in out


async def test_mixed_alphabet_meta_line_from_tools_falls_back(monkeypatch):
    # Одна мета-строка со смешанным алфавитом без тела — раньше проходила по «кириллице».
    _tools_on(monkeypatch)
    calls = []

    async def _post(path, payload, **kw):
        calls.append(payload)
        if "mcp_servers" in payload:
            return {"text": "The вопрос владельца: помнит ли он что спрашивал ранее."}
        return {"text": "Да, помню: вы спрашивали про блокировки."}

    monkeypatch.setattr(ai_gateway, "_gateway_post", _post)
    out = await ai_gateway._answer_owner_query_http("помнишь?", "СНИМОК")
    assert out == "Да, помню: вы спрашивали про блокировки." and len(calls) == 2


async def test_dispatcher_guards_api_and_cli_backends(monkeypatch):
    # Защита одна на все бэкенды: api/cli не отдают владельцу служебную строку.
    for backend, fn in (("api", "_answer_owner_query_api"), ("cli", "_answer_owner_query_cli")):
        monkeypatch.setattr(ai_gateway, "_resolve_backend", lambda b=backend: b)

        async def _meta(*a, **k):
            return _META_ONLY

        async def _num(*a, **k):
            return "12 500"

        monkeypatch.setattr(ai_gateway, fn, _meta)
        assert await ai_gateway.answer_owner_query("?", "С") == ai_gateway._NO_ANSWER
        monkeypatch.setattr(ai_gateway, fn, _num)
        assert await ai_gateway.answer_owner_query("сумма?", "С") == "12 500"


def test_is_answer():
    ok = ai_gateway._is_answer
    assert ok("Да, помню.")
    assert ok("01KG138API — 4 штрафа")
    # валидные ответы без кириллицы — не отбрасываем
    assert ok("138AAA02") and ok("12 500") and ok("Hyundai Sonata 2019")
    # голое латинское название из 3+ слов — тоже валидный ответ (замечание ревью)
    assert ok("Toyota Land Cruiser") and ok("Kia Rio X-Line")
    # мусор вместо ответа
    assert not ok("") and not ok("   ")
    assert not ok(_META_ONLY)
    assert not ok("The user asks: кто заблокирован?")           # смешанный алфавит
    assert not ok("The вопрос владельца: помнит ли он что спрашивал ранее.")
    assert not ok("I can answer directly from the conversation history.")
