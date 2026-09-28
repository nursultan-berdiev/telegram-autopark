"""Клиент tolom.kg: исходы проверки одного номера."""
from __future__ import annotations

import json

import httpx
import pytest

from app.tolom.client import ENDPOINT, USER_AGENT, TolomSession

PAYLOAD_OK = {
    "currentInfo": {"success": True, "data": {"brand": "HYUNDAI"}},
    "penalties": {"success": True, "data": {"bgProtocols": [], "erpnProtocols": []}},
    "totalPenalties": 0,
}
PAYLOAD_UNKNOWN_PLATE = {
    "currentInfo": {"success": False, "message": "NOT_FOUND", "data": None},
    "penalties": {"success": False, "message": "NOT_FOUND", "data": None},
    "totalPenalties": 0,
}


def _session(handler) -> TolomSession:
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.invalid/api/v1"
    )
    return TolomSession(client)


def test_sends_plate_in_body_to_the_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=PAYLOAD_OK)

    _session(handler).check("01KG000AAA")

    assert seen["path"].endswith(ENDPOINT)
    assert seen["body"]["plate"] == "01KG000AAA"


def test_user_agent_does_not_pretend_to_be_a_browser():
    """WAF пропускает честное имя — притворяться браузером незачем."""
    assert "Mozilla" not in USER_AGENT
    assert "TelegramAutopark" in USER_AGENT


def test_ok_response_is_a_payload():
    result = _session(lambda r: httpx.Response(200, json=PAYLOAD_OK)).check("A")

    assert result.ok
    assert result.plate_known is True


def test_unknown_plate_is_marked():
    """Номера нет в реестре — это не «штрафов нет»."""
    result = _session(lambda r: httpx.Response(200, json=PAYLOAD_UNKNOWN_PLATE)).check("A")

    assert result.ok
    assert result.plate_known is False


@pytest.mark.parametrize("status", [403, 429])
def test_refusal_is_not_a_failure(status):
    """Отказ относится к нам, а не к номеру: обход парка обязан встать."""
    result = _session(lambda r: httpx.Response(status)).check("A")

    assert result.refused is not None
    assert result.error is None
    assert not result.ok


def test_server_error_is_a_failure():
    result = _session(lambda r: httpx.Response(500)).check("A")

    assert result.error == "HTTP 500"
    assert result.refused is None


def test_non_json_answer_is_a_failure():
    result = _session(lambda r: httpx.Response(200, text="<html>")).check("A")

    assert result.error is not None
    assert not result.ok


def test_non_object_json_is_a_failure():
    """Список вместо объекта — не та форма, разбирать нечего."""
    result = _session(lambda r: httpx.Response(200, json=[1, 2])).check("A")

    assert result.error is not None


def test_network_error_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    result = _session(handler).check("A")

    assert result.error is not None
    assert "ConnectError" in result.error
