"""Вечерние уведомления у срока: доставка водителям/админам и разбор времени."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

import app.scheduler as scheduler
from app.client import ApiError
from app.scheduler import _parse_hm, send_admin_digest, send_block_notices, send_overdue_warnings


class _Bot:
    def __init__(self, fail_ids: tuple[int, ...] = ()) -> None:
        self.sent: list[int] = []
        self.fail_ids = set(fail_ids)

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if chat_id in self.fail_ids:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append(chat_id)


class _Api:
    def __init__(self, notices=None, *, digest=None, fail_fetch=False) -> None:
        self._notices = notices or []
        self._digest = digest
        self.fail_fetch = fail_fetch
        self.marked: list[list[int]] = []

    async def _plan(self, now=None, *, force=False) -> dict:
        if self.fail_fetch:
            raise ApiError("core-api недоступен")
        return {"notices": self._notices}

    reminders_overdue_warning = _plan
    reminders_block_notice = _plan

    async def reminders_mark_warning(self, schedule_ids, on_date=None) -> None:
        self.marked.append(schedule_ids)

    async def reminders_mark_block_notice(self, schedule_ids, on_date=None) -> None:
        self.marked.append(schedule_ids)

    async def reminders_admin_digest(self, now=None) -> dict:
        if self.fail_fetch:
            raise ApiError("core-api недоступен")
        return {"text": self._digest}


async def test_warnings_mark_only_actually_sent():
    notices = [
        {"schedule_id": 1, "tg_user_id": 10, "text": "a"},
        {"schedule_id": 2, "tg_user_id": 20, "text": "b"},
    ]
    bot, api = _Bot(fail_ids=(20,)), _Api(notices)
    out = await send_overdue_warnings(bot, api)
    assert bot.sent == [10]
    assert api.marked == [[1]]  # только реально отправленный график
    assert out == {"drivers": 1}


async def test_warnings_fetch_error_is_swallowed():
    bot, api = _Bot(), _Api(fail_fetch=True)
    out = await send_overdue_warnings(bot, api)
    assert out == {"drivers": 0} and bot.sent == [] and api.marked == []


async def test_block_notices_sent_and_marked():
    bot, api = _Bot(), _Api([{"schedule_id": 7, "tg_user_id": 70, "text": "блок"}])
    out = await send_block_notices(bot, api)
    assert bot.sent == [70] and api.marked == [[7]] and out == {"drivers": 1}


async def test_admin_digest_empty_text_sends_nothing(monkeypatch):
    monkeypatch.setattr(scheduler.settings, "admin_ids", [1, 2])
    bot, api = _Bot(), _Api(digest=None)
    out = await send_admin_digest(bot, api)
    assert out == {"owners": 0} and bot.sent == []


async def test_admin_digest_goes_to_all_admins(monkeypatch):
    monkeypatch.setattr(scheduler.settings, "admin_ids", [1, 2])
    bot, api = _Bot(), _Api(digest="сводка")
    out = await send_admin_digest(bot, api)
    assert out == {"owners": 2} and bot.sent == [1, 2]


def test_parse_hm_ok():
    assert _parse_hm("21:45") == (21, 45) and _parse_hm("09:00") == (9, 0)


@pytest.mark.parametrize(
    "alias", ["PAYMENT_WARN_AT", "PAYMENT_BLOCK_NOTICE_AT", "PAYMENT_DIGEST_AT"]
)
@pytest.mark.parametrize("bad", ["22.00", "2200", "25:00", "", "abc"])
def test_config_rejects_bad_time(alias, bad):
    # Значение передаём по АЛИАСУ (как из env): поле без populate_by_name не
    # принимает имя поля в конструкторе — иначе дефолт, и валидатор не сработает.
    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(**{alias: bad})


@pytest.mark.parametrize(
    "alias", ["PAYMENT_WARN_AT", "PAYMENT_BLOCK_NOTICE_AT", "PAYMENT_DIGEST_AT"]
)
def test_config_accepts_valid_time(alias):
    from app.config import Settings

    assert Settings(**{alias: "21:45"})  # валидный формат не роняет
