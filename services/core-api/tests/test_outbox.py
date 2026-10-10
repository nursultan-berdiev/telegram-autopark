"""Очередь исходящих: enqueue / pending / sent / failed (permanent + лимит)."""
from app.domain import outbox


async def test_enqueue_appears_in_pending(session):
    m = await outbox.enqueue(session, recipient_tg_user_id=10, text="привет", kind="reminder")
    await session.commit()
    rows = await outbox.list_pending(session)
    assert len(rows) == 1
    assert rows[0].id == m.id and rows[0].recipient_tg_user_id == 10
    assert rows[0].text == "привет" and rows[0].sent_at is None and rows[0].failed_at is None


async def test_mark_sent_removes_from_pending(session):
    m = await outbox.enqueue(session, recipient_tg_user_id=10, text="a", kind="digest")
    await session.commit()
    assert await outbox.mark_sent(session, m.id) is True
    assert await outbox.list_pending(session) == []


async def test_pending_is_fifo(session):
    first = await outbox.enqueue(session, recipient_tg_user_id=1, text="1", kind="warn")
    second = await outbox.enqueue(session, recipient_tg_user_id=2, text="2", kind="warn")
    await session.commit()
    rows = await outbox.list_pending(session)
    assert [r.id for r in rows] == [first.id, second.id]


async def test_permanent_failure_gives_up_distinct_from_sent(session):
    m = await outbox.enqueue(session, recipient_tg_user_id=10, text="a", kind="warn")
    await session.commit()
    assert await outbox.mark_failed(session, m.id, permanent=True) is True
    assert await outbox.list_pending(session) == []
    refreshed = await session.get(outbox.OutboundMessage, m.id)
    # «Сдались» ≠ «доставлено»: failed_at есть, sent_at пуст.
    assert refreshed.failed_at is not None and refreshed.sent_at is None


async def test_transient_failures_retry_until_limit(session):
    m = await outbox.enqueue(session, recipient_tg_user_id=10, text="a", kind="warn")
    await session.commit()
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        await outbox.mark_failed(session, m.id, permanent=False)
    # Ещё не исчерпал лимит — остаётся в выдаче (краткий сбой переживается).
    assert len(await outbox.list_pending(session)) == 1
    await outbox.mark_failed(session, m.id, permanent=False)  # лимит достигнут
    assert await outbox.list_pending(session) == []
    refreshed = await session.get(outbox.OutboundMessage, m.id)
    assert refreshed.attempts == outbox.MAX_ATTEMPTS and refreshed.failed_at is not None


async def test_mark_on_missing_id_returns_false(session):
    assert await outbox.mark_sent(session, 999999) is False
    assert await outbox.mark_failed(session, 999999, permanent=True) is False
