"""Интеграция с Claude: распознавание чеков и (Этап 6) аналитика владельца.

Поддерживаются два backend'а (выбор — settings.ai_backend):

- "api" — прямой вызов Anthropic API (нужен ANTHROPIC_API_KEY). Vision +
  принудительный tool use (`report_receipt`) — максимально надёжно.
- "cli" — Claude Code в режиме `claude -p` (работает на ПОДПИСКЕ Anthropic,
  без API-ключа). Чек сохраняется во временный файл и читается инструментом
  Read; ответ запрашивается строгим JSON.
- "auto" — api при заданном ключе, иначе cli.

Публичные функции `recognize_receipt` / `answer_owner_query` диспетчеризуют
вызов на выбранный backend; форма результата одинакова.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime

import httpx
from anthropic import AsyncAnthropic

from bot.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RecognizedReceipt:
    readable: bool
    amount: float | None
    currency: str | None
    paid_at: datetime | None
    paid_at_raw: str | None
    note: str | None


# --------------------------------------------------------------------------
# Общие описания/промпты (используются обоими backend'ами)
# --------------------------------------------------------------------------

_RECEIPT_FIELDS = (
    '{"readable": boolean — удалось ли распознать платёжный чек, '
    '"amount": number|null — сумма платежа без валюты, '
    '"currency": string|null — валюта (код или символ), '
    '"paid_at": string|null — дата и время платежа в формате ISO 8601, '
    '"note": string|null — краткое пояснение при проблемах}'
)

_RECEIPT_TOOL = {
    "name": "report_receipt",
    "description": "Сообщить распознанные данные чека об оплате.",
    "input_schema": {
        "type": "object",
        "properties": {
            "readable": {
                "type": "boolean",
                "description": "Удалось ли распознать изображение как платёжный чек.",
            },
            "amount": {
                "type": ["number", "null"],
                "description": "Сумма платежа числом (без валюты).",
            },
            "currency": {
                "type": ["string", "null"],
                "description": "Валюта платежа (код или символ), если указана.",
            },
            "paid_at": {
                "type": ["string", "null"],
                "description": "Дата и время платежа в формате ISO 8601, если есть.",
            },
            "note": {
                "type": ["string", "null"],
                "description": "Краткое пояснение при проблемах с распознаванием.",
            },
        },
        "required": ["readable", "amount", "currency", "paid_at"],
    },
}

_PROMPT = (
    "На изображении — чек об оплате (перевод/квитанция). Извлеки сумму платежа, "
    "валюту и дату/время платежа. Если это не платёжный чек или данные не читаются, "
    "укажи readable=false. Верни результат только через инструмент report_receipt."
)

_OWNER_SYSTEM = (
    "Ты — ассистент владельца автопарка. Отвечай кратко и по делу на русском языке, "
    "опираясь ТОЛЬКО на приведённые данные автопарка. Если данных для ответа "
    "недостаточно, честно скажи об этом. Не выдумывай цифры."
)


# --------------------------------------------------------------------------
# Разбор результата (общий для обоих backend'ов)
# --------------------------------------------------------------------------


def _parse_receipt(data: dict) -> RecognizedReceipt:
    amount = data.get("amount")
    try:
        amount_val = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_val = None

    paid_at_raw = data.get("paid_at")
    paid_at = _parse_dt(paid_at_raw)

    return RecognizedReceipt(
        readable=bool(data.get("readable")),
        amount=amount_val,
        currency=data.get("currency"),
        paid_at=paid_at,
        paid_at_raw=paid_at_raw if isinstance(paid_at_raw, str) else None,
        note=data.get("note"),
    )


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _extract_json(text: str) -> dict:
    """Достаёт JSON-объект из свободного текста ответа CLI (без markdown/пояснений)."""
    if not text:
        return {}
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {}


# --------------------------------------------------------------------------
# Выбор backend'а
# --------------------------------------------------------------------------


def _cli_available() -> bool:
    return shutil.which(settings.claude_cli_path) is not None


_BACKENDS = ("http", "api", "cli")


def _resolve_backend() -> str:
    backend = (settings.ai_backend or "auto").lower()
    if backend not in ("auto", *_BACKENDS):
        raise RuntimeError(
            f"AI_BACKEND={settings.ai_backend!r} не поддерживается; "
            f"допустимо: auto, {', '.join(_BACKENDS)}."
        )
    if backend == "auto":
        if settings.gateway_url:
            return "http"
        if settings.anthropic_api_key:
            return "api"
        if _cli_available():
            return "cli"
        raise RuntimeError(
            "ИИ не настроен: нет GATEWAY_URL, нет ANTHROPIC_API_KEY и не найден "
            "claude CLI (проверьте AI_BACKEND)."
        )
    return backend


# --------------------------------------------------------------------------
# Публичный API (диспетчеризация)
# --------------------------------------------------------------------------


async def recognize_receipt(image_bytes: bytes, media_type: str) -> RecognizedReceipt:
    """Распознаёт чек выбранным backend'ом. Бросает исключение при ошибке сервиса."""
    backend = _resolve_backend()
    if backend == "http":
        return await _recognize_receipt_http(image_bytes, media_type)
    if backend == "cli":
        return await _recognize_receipt_cli(image_bytes, media_type)
    return await _recognize_receipt_api(image_bytes, media_type)


async def answer_owner_query(question: str, context_text: str) -> str:
    """Отвечает на свободный вопрос владельца по снимку данных (FR-AI-6)."""
    backend = _resolve_backend()
    if backend == "http":
        return await _answer_owner_query_http(question, context_text)
    if backend == "cli":
        return await _answer_owner_query_cli(question, context_text)
    return await _answer_owner_query_api(question, context_text)


# --------------------------------------------------------------------------
# Backend "api" (Anthropic SDK) — как было
# --------------------------------------------------------------------------

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def _recognize_receipt_api(
    image_bytes: bytes, media_type: str
) -> RecognizedReceipt:
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    client = _get_client()

    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        tools=[_RECEIPT_TOOL],
        tool_choice={"type": "tool", "name": "report_receipt"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )

    data: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "report_receipt":
            data = block.input  # type: ignore[assignment]
            break

    return _parse_receipt(data)


async def _answer_owner_query_api(question: str, context_text: str) -> str:
    client = _get_client()
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=_OWNER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Данные автопарка:\n{context_text}\n\n"
                    f"Вопрос владельца: {question}"
                ),
            }
        ],
    )
    parts = [b.text for b in response.content if b.type == "text"]
    return "\n".join(parts).strip() or "Не удалось сформировать ответ."


# --------------------------------------------------------------------------
# Backend "cli" (Claude Code `claude -p`, подписка)
# --------------------------------------------------------------------------

_MEDIA_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


async def _run_claude_cli(args: list[str]) -> dict:
    """Запускает `claude -p ... --output-format json`, возвращает распарсенный envelope.

    Изолирует subprocess-вызов (в тестах подменяется моком).
    """
    proc = await asyncio.create_subprocess_exec(
        settings.claude_cli_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=settings.ai_timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Claude CLI: превышено время ожидания ответа.")

    if proc.returncode != 0:
        tail = err.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Claude CLI завершился с кодом {proc.returncode}: {tail}")

    envelope = json.loads(out.decode("utf-8", errors="replace"))
    if envelope.get("is_error"):
        detail = str(envelope.get("result"))[:300]
        raise RuntimeError(f"Claude CLI вернул ошибку: {detail}")
    return envelope


def _cli_receipt_prompt(path: str) -> str:
    return (
        f"Прочитай изображение платёжного чека по абсолютному пути {path} "
        "с помощью инструмента Read. Извлеки сумму платежа, валюту и дату/время "
        "платежа. Если это не платёжный чек или данные не читаются — readable=false. "
        "Ответь СТРОГО одним JSON-объектом без markdown и пояснений вида "
        f"{_RECEIPT_FIELDS}."
    )


async def _recognize_receipt_cli(
    image_bytes: bytes, media_type: str
) -> RecognizedReceipt:
    ext = _MEDIA_EXT.get(media_type, ".jpg")
    fd, path = tempfile.mkstemp(suffix=ext, prefix="receipt_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(image_bytes)
        args = [
            "-p",
            _cli_receipt_prompt(path),
            "--output-format",
            "json",
            "--allowedTools",
            "Read",
            "--add-dir",
            os.path.dirname(path),
            "--model",
            settings.claude_cli_model,
        ]
        envelope = await _run_claude_cli(args)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    data = _extract_json(str(envelope.get("result") or ""))
    return _parse_receipt(data)


def _cli_owner_prompt(question: str, context_text: str) -> str:
    return (
        f"{_OWNER_SYSTEM}\n\n"
        f"Данные автопарка:\n{context_text}\n\n"
        f"Вопрос владельца: {question}\n\n"
        "Ответь текстом для пользователя, не используй инструменты."
    )


async def _answer_owner_query_cli(question: str, context_text: str) -> str:
    args = [
        "-p",
        _cli_owner_prompt(question, context_text),
        "--output-format",
        "json",
        "--model",
        settings.claude_cli_model,
    ]
    envelope = await _run_claude_cli(args)
    return str(envelope.get("result") or "").strip() or "Не удалось сформировать ответ."


# --------------------------------------------------------------------------
# Backend "http" (claude-gateway)
# --------------------------------------------------------------------------
# Подписка живёт в шлюзе: боту не нужен ни API-ключ, ни claude в образе —
# поэтому именно этот backend используется в docker.

_RECEIPT_SCHEMA = {
    "readable": "boolean — удалось ли распознать платёжный чек",
    "amount": "number|null — сумма платежа без валюты",
    "currency": "string|null — валюта (код или символ)",
    "paid_at": "string|null — дата и время платежа в ISO 8601",
    "note": "string|null — краткое пояснение при проблемах",
}

_RECEIPT_INSTRUCTION = (
    "На изображении — чек об оплате (перевод/квитанция). Извлеки сумму платежа, "
    "валюту и дату/время платежа. Если это не платёжный чек или данные не "
    "читаются — readable=false."
)


async def _gateway_post(path: str, payload: dict) -> dict:
    """POST в claude-gateway. Бросает исключение при сетевой/HTTP-ошибке."""
    headers = {}
    if settings.gateway_api_key:
        headers["Authorization"] = f"Bearer {settings.gateway_api_key}"

    url = settings.gateway_url.rstrip("/") + path
    async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def _recognize_receipt_http(
    image_bytes: bytes, media_type: str
) -> RecognizedReceipt:
    body = await _gateway_post(
        "/v1/extract",
        {
            "image_base64": base64.standard_b64encode(image_bytes).decode("utf-8"),
            "media_type": media_type,
            "instruction": _RECEIPT_INSTRUCTION,
            "schema": _RECEIPT_SCHEMA,
            "model": settings.gateway_model,
        },
    )
    return _parse_receipt(body.get("data") or {})


async def _answer_owner_query_http(question: str, context_text: str) -> str:
    body = await _gateway_post(
        "/v1/prompt",
        {
            "prompt": _cli_owner_prompt(question, context_text),
            "model": settings.gateway_model,
        },
    )
    return str(body.get("text") or "").strip() or "Не удалось сформировать ответ."
