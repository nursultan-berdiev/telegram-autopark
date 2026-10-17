"""Объявления: публикация с рассылкой через outbox, история, мягкое удаление."""
from __future__ import annotations

from sqlalchemy import select

import pytest

from app.db.models import Announcement, Car, Driver, OutboundMessage
from app.domain import announcements, outbox
from app.errors import Validation
from contracts import ANNOUNCEMENT_MAX_TEXT, telegram_length

ADMIN_ID = 111


async def _driver(session, *, tg: int, active: bool = True) -> Driver:
    car = Car(plate=f"01KG{tg}AAA")
    session.add(car)
    await session.flush()
    driver = Driver(
        tg_user_id=tg, full_name=f"Вод {tg}", phone="+1", inn=str(tg),
        car_id=car.id if active else None, active=active,
    )
    session.add(driver)
    await session.commit()
    return driver


async def _outbox(session) -> list[OutboundMessage]:
    return list(await session.scalars(select(OutboundMessage).order_by(OutboundMessage.id)))


# --- домен ------------------------------------------------------------------


async def test_publish_queues_message_for_every_active_driver(session):
    await _driver(session, tg=501)
    await _driver(session, tg=502)
    await _driver(session, tg=503, active=False)  # уволенный рассылку не получает

    ann = await announcements.publish(
        session, text="Скидка 1500 сом с 1 января", created_by=ADMIN_ID
    )

    rows = await _outbox(session)
    assert sorted(r.recipient_tg_user_id for r in rows) == [501, 502]
    assert {r.kind for r in rows} == {"announcement"}
    assert all("Скидка 1500 сом с 1 января" in r.text for r in rows)
    assert ann.recipients == 2 and ann.created_by == ADMIN_ID


async def test_publish_escapes_admin_html(session):
    """parse_mode=HTML у бота глобальный: «<» в тексте иначе уронил бы отправку."""
    await _driver(session, tg=501)

    await announcements.publish(session, text="Аренда <1500> & скидка", created_by=ADMIN_ID)

    (row,) = await _outbox(session)
    assert "Аренда &lt;1500&gt; &amp; скидка" in row.text


async def test_publish_without_drivers_is_stored_with_zero_recipients(session):
    ann = await announcements.publish(session, text="Правила парка", created_by=ADMIN_ID)

    assert ann.recipients == 0
    assert await _outbox(session) == []
    assert [a.id for a in await announcements.list_announcements(session)] == [ann.id]


async def test_history_is_newest_first_and_hides_deleted(session):
    first = await announcements.publish(session, text="первое", created_by=ADMIN_ID)
    second = await announcements.publish(session, text="второе", created_by=ADMIN_ID)
    third = await announcements.publish(session, text="третье", created_by=ADMIN_ID)

    assert await announcements.delete_announcement(session, second.id) is True

    assert [a.id for a in await announcements.list_announcements(session)] == [
        third.id, first.id,
    ]
    assert await announcements.get_announcement(session, second.id) is None
    # Запись остаётся: что разослали водителям, видно постфактум.
    assert (await session.get(Announcement, second.id)).deleted_at is not None


async def test_delete_cancels_unsent_broadcast_only(session):
    """Ошибочное объявление, удалённое сразу, не должно дойти до остальных водителей."""
    await _driver(session, tg=501)
    await _driver(session, tg=502)
    other = await outbox.enqueue(session, recipient_tg_user_id=501, text="аренда", kind="reminder")
    ann = await announcements.publish(session, text="ошибка", created_by=ADMIN_ID)
    delivered, pending = [r for r in await _outbox(session) if r.kind == "announcement"]
    await outbox.mark_sent(session, delivered.id)

    await announcements.delete_announcement(session, ann.id)

    assert [r.id for r in await outbox.list_pending(session)] == [other.id]
    for row in (delivered, pending):
        await session.refresh(row)
    assert delivered.sent_at is not None and delivered.cancelled_at is None
    # Отмена — не сбой доставки: failed_at пуст, отметка своя.
    assert pending.sent_at is None and pending.failed_at is None
    assert pending.cancelled_at is not None


async def test_length_is_counted_like_telegram(session):
    """Лимит Telegram — после разбора entities и в UTF-16: «&» — один символ, эмодзи — два."""
    assert telegram_length("&") == 1 and telegram_length("🚗") == 2

    near_limit = await announcements.publish(
        session, text="&" * ANNOUNCEMENT_MAX_TEXT, created_by=ADMIN_ID
    )
    assert near_limit.id
    with pytest.raises(Validation):
        await announcements.publish(
            session, text="🚗" * (ANNOUNCEMENT_MAX_TEXT // 2 + 1), created_by=ADMIN_ID
        )


async def test_delete_twice_reports_missing(session):
    ann = await announcements.publish(session, text="x", created_by=ADMIN_ID)
    assert await announcements.delete_announcement(session, ann.id) is True
    assert await announcements.delete_announcement(session, ann.id) is False
    assert await announcements.delete_announcement(session, 999) is False


# --- API --------------------------------------------------------------------


async def test_api_publish_and_read(admin_client, session):
    await _driver(session, tg=501)

    created = await admin_client.post("/announcements", json={"text": "  Новые правила  "})
    assert created.status_code == 201
    body = created.json()
    assert body["text"] == "Новые правила" and body["recipients"] == 1
    assert body["created_by"] == ADMIN_ID

    listed = await admin_client.get("/announcements")
    assert [a["id"] for a in listed.json()] == [body["id"]]
    one = await admin_client.get(f"/announcements/{body['id']}")
    assert one.status_code == 200 and one.json()["text"] == "Новые правила"


async def test_api_publish_requires_admin(client, session):
    await _driver(session, tg=501)

    as_driver = await client.post(
        "/announcements", json={"text": "фейк"}, headers={"X-TG-User-Id": "501"}
    )
    anonymous = await client.post("/announcements", json={"text": "фейк"})

    assert as_driver.status_code == 403 and anonymous.status_code == 403
    assert await _outbox(session) == []


async def test_api_rejects_empty_and_too_long(admin_client, session):
    await _driver(session, tg=501)

    empty = await admin_client.post("/announcements", json={"text": "   "})
    too_long = await admin_client.post(
        "/announcements", json={"text": "я" * (ANNOUNCEMENT_MAX_TEXT + 1)}
    )

    assert empty.status_code == 422 and too_long.status_code == 422
    assert await _outbox(session) == []


async def test_api_history_limit_bounds(admin_client):
    assert (await admin_client.get("/announcements", params={"limit": 0})).status_code == 422
    assert (await admin_client.get("/announcements", params={"limit": 201})).status_code == 422
    assert (await admin_client.get("/announcements", params={"limit": 200})).status_code == 200


async def test_api_delete(admin_client, client):
    created = (await admin_client.post("/announcements", json={"text": "x"})).json()

    forbidden = await client.delete(
        f"/announcements/{created['id']}", headers={"X-TG-User-Id": "501"}
    )
    assert forbidden.status_code == 403

    assert (await admin_client.delete(f"/announcements/{created['id']}")).status_code == 204
    assert (await admin_client.get(f"/announcements/{created['id']}")).status_code == 404
    assert (await admin_client.delete(f"/announcements/{created['id']}")).status_code == 404
    assert (await admin_client.get("/announcements")).json() == []
