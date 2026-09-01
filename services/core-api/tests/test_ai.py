"""Тесты ИИ-слоя: выбор backend, парсинг JSON, CLI-путь (без реального subprocess)."""
import pytest

from app.clients import ai_gateway as ai


# ----------------------------------------------------------------- _extract_json
def test_extract_json_plain():
    assert ai._extract_json('{"readable": true, "amount": 10}') == {
        "readable": True,
        "amount": 10,
    }


def test_extract_json_fenced():
    text = '```json\n{"amount": 1500.0, "currency": "KGS"}\n```'
    assert ai._extract_json(text) == {"amount": 1500.0, "currency": "KGS"}


def test_extract_json_embedded_in_prose():
    text = 'Вот результат: {"readable": false, "note": "не чек"} — готово.'
    assert ai._extract_json(text) == {"readable": False, "note": "не чек"}


def test_extract_json_garbage_returns_empty():
    assert ai._extract_json("нет тут json") == {}
    assert ai._extract_json("") == {}


# --------------------------------------------------------------- backend resolve
def test_resolve_backend_auto_prefers_http(monkeypatch):
    """Шлюз важнее ключа: в docker у бота нет ни ключа, ни claude."""
    monkeypatch.setattr(ai.settings, "ai_backend", "auto")
    monkeypatch.setattr(ai.settings, "gateway_url", "http://gw:8080")
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-x")
    assert ai._resolve_backend() == "http"


def test_resolve_backend_auto_prefers_api(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "auto")
    monkeypatch.setattr(ai.settings, "gateway_url", "")
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "sk-x")
    assert ai._resolve_backend() == "api"


def test_resolve_backend_auto_falls_back_to_cli(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "auto")
    monkeypatch.setattr(ai.settings, "gateway_url", "")
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    monkeypatch.setattr(ai, "_cli_available", lambda: True)
    assert ai._resolve_backend() == "cli"


def test_resolve_backend_auto_none_available_raises(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "auto")
    monkeypatch.setattr(ai.settings, "gateway_url", "")
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "")
    monkeypatch.setattr(ai, "_cli_available", lambda: False)
    with pytest.raises(RuntimeError):
        ai._resolve_backend()


# ------------------------------------------------------------- HTTP (шлюз)
async def test_recognize_receipt_http(monkeypatch):
    captured = {}

    async def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {
            "data": {
                "readable": True,
                "amount": 1500.0,
                "currency": "KGS",
                "paid_at": "2026-07-05T14:30:00",
                "note": None,
            }
        }

    monkeypatch.setattr(ai, "_gateway_post", fake_post)
    rec = await ai._recognize_receipt_http(b"bytes", "image/png")

    assert rec.readable is True and rec.amount == 1500.0 and rec.currency == "KGS"
    assert rec.paid_at_raw == "2026-07-05T14:30:00"
    assert captured["path"] == "/v1/extract"
    assert captured["payload"]["media_type"] == "image/png"
    assert "schema" in captured["payload"]


async def test_recognize_receipt_http_unreadable(monkeypatch):
    async def fake_post(path, payload):
        return {"data": {}}

    monkeypatch.setattr(ai, "_gateway_post", fake_post)
    rec = await ai._recognize_receipt_http(b"x", "image/jpeg")
    assert rec.readable is False and rec.amount is None


async def test_answer_owner_query_http(monkeypatch):
    async def fake_post(path, payload):
        assert path == "/v1/prompt"
        assert "Данные автопарка" in payload["prompt"]
        return {"text": "Свободны 2 машины."}

    monkeypatch.setattr(ai, "_gateway_post", fake_post)
    assert await ai._answer_owner_query_http("вопрос", "ктx") == "Свободны 2 машины."


def test_resolve_backend_explicit(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "cli")
    assert ai._resolve_backend() == "cli"


# ------------------------------------------------------------------ CLI receipt
async def test_recognize_receipt_cli_parses_result(monkeypatch):
    captured = {}

    async def fake_run(args):
        captured["args"] = args
        return {
            "is_error": False,
            "result": '{"readable":true,"amount":1500.0,"currency":"KGS",'
            '"paid_at":"2026-07-05T14:30:00","note":null}',
        }

    monkeypatch.setattr(ai, "_run_claude_cli", fake_run)
    rec = await ai._recognize_receipt_cli(b"fakebytes", "image/png")

    assert rec.readable is True
    assert rec.amount == 1500.0
    assert rec.currency == "KGS"
    assert rec.paid_at_raw == "2026-07-05T14:30:00"
    # CLI получает Read и модель из конфига
    assert "Read" in captured["args"]
    assert "--output-format" in captured["args"]


async def test_recognize_receipt_cli_unreadable_on_garbage(monkeypatch):
    async def fake_run(args):
        return {"is_error": False, "result": "не удалось распознать"}

    monkeypatch.setattr(ai, "_run_claude_cli", fake_run)
    rec = await ai._recognize_receipt_cli(b"x", "image/jpeg")
    assert rec.readable is False and rec.amount is None


async def test_answer_owner_query_cli(monkeypatch):
    async def fake_run(args):
        return {"is_error": False, "result": "Свободны 2 машины."}

    monkeypatch.setattr(ai, "_run_claude_cli", fake_run)
    ans = await ai._answer_owner_query_cli("сколько свободно", "контекст")
    assert ans == "Свободны 2 машины."


async def test_public_recognize_routes_to_cli(monkeypatch):
    monkeypatch.setattr(ai, "_resolve_backend", lambda: "cli")

    async def fake_cli(image_bytes, media_type):
        return ai.RecognizedReceipt(True, 1.0, "KGS", None, None, None)

    monkeypatch.setattr(ai, "_recognize_receipt_cli", fake_cli)
    rec = await ai.recognize_receipt(b"x", "image/png")
    assert rec.amount == 1.0


def test_unknown_backend_fails_loudly(monkeypatch):
    """Опечатка в AI_BACKEND раньше молча уходила в API-ветку и падала глубже."""
    import pytest

    from app.config import settings

    monkeypatch.setattr(settings, "ai_backend", "cll")
    with pytest.raises(RuntimeError, match="не поддерживается"):
        ai._resolve_backend()
