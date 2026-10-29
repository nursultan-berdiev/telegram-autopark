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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from anthropic import AsyncAnthropic

from app.config import settings

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


def _owner_tools_system() -> str:
    """Системный промпт режима инструментов: общий принцип поведения агента.

    Без частных правил (никаких «если 138 — смотри парк»): модель сама понимает
    намерение и подбирает инструменты. Подробности каждого инструмента — в его
    описании на MCP-сервере, здесь их не дублируем.
    """
    now = datetime.now(ZoneInfo(settings.timezone))
    return (
        "Ты — ассистент владельца автопарка; к тебе обращается только владелец. "
        f"Сейчас {now:%Y-%m-%d %H:%M} ({settings.timezone}). "
        "Отвечай кратко, по делу, на русском.\n"
        "У тебя есть инструменты MCP-сервера fleet для ЧТЕНИЯ данных парка — их "
        "названия, параметры, что возвращают и ограничения описаны в самих "
        "инструментах. Действуй так:\n"
        "1. Сначала пойми, что на самом деле нужно человеку.\n"
        "2. Реши, каким инструментом или их сочетанием это можно получить, и вызови.\n"
        "3. Проверь, что полученные данные действительно отвечают на вопрос.\n"
        "4. Если точного совпадения нет (например, назван неполный или неточный "
        "номер машины) — поищи близкое другими инструментами (например, посмотри "
        "список парка) и предложи владельцу подходящие варианты.\n"
        "5. Если нужных данных не даёт ни один инструмент — честно скажи, что не "
        "можешь это получить, и предложи обратиться к разработчику; не придумывай "
        "обходной ответ.\n"
        "Опирайся ТОЛЬКО на данные инструментов — не выдумывай ни номеров, ни сумм, "
        "ни фактов. Это продолжающийся разговор: учитывай предыдущие реплики.\n"
        "Выдавай пользователю только готовый ответ на русском — без служебных "
        "рассуждений, без пересказа вопроса и без текста на других языках. Время в "
        "данных инструментов уже в часовом поясе парка — показывай как есть, не "
        "пересчитывай из UTC."
    )


def _history_lines(history: Sequence[Any] | None) -> list[str]:
    """Реплики разговора в виде строк «Владелец: …» / «Ассистент: …»."""
    lines: list[str] = []
    for turn in history or []:
        who = "Владелец" if getattr(turn, "role", "user") == "user" else "Ассистент"
        lines.append(f"{who}: {getattr(turn, 'text', '')}")
    return lines


def _compose_owner_prompt(question: str, history: Sequence[Any] | None) -> str:
    """Вопрос владельца с учётом истории текущего разговора (для режима инструментов)."""
    prior = _history_lines(history)
    if not prior:
        return question
    return (
        "Это продолжение текущего разговора. Предыдущие реплики:\n"
        + "\n".join(prior)
        + f"\n\nНовый вопрос владельца: {question}"
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


async def answer_owner_query(
    question: str, context_text: str, history: Sequence[Any] | None = None
) -> str:
    """Отвечает на вопрос владельца (FR-AI-6).

    `history` — реплики текущего разговора (без самого `question`); передаётся
    модели на каждом ходу, чтобы держался контекст беседы «Спросить ИИ».
    """
    backend = _resolve_backend()
    if backend == "http":
        text = await _answer_owner_query_http(question, context_text, history)
    elif backend == "cli":
        text = await _answer_owner_query_cli(question, context_text, history)
    else:
        text = await _answer_owner_query_api(question, context_text, history)
    return _strip_meta_preamble(text)


_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_NO_ANSWER = "Не удалось сформировать ответ — переформулируйте, пожалуйста, вопрос."


def _is_answer(text: str) -> bool:
    """Годный ответ владельцу: непустой и по-русски (есть кириллица).

    Opus изредка вместо ответа отдаёт только служебную строку на английском —
    срез мета-строки тут бессилен (тела после неё нет), поэтому проверяем сам
    ответ и при неудаче идём в фолбэк, а не показываем мусор.
    """
    return bool(text and _CYRILLIC.search(text))


def _strip_meta_preamble(text: str) -> str:
    """Срезает ведущую служебную строку-пересказ вопроса, которую Opus изредка
    выдаёт перед ответом («The user asks…», «The вопрос владельца:…»).

    Детерминированная подстраховка к промпту (он просит так не делать, но модель
    недетерминирована). Режем ТОЛЬКО первую строку и ТОЛЬКО если она начинается с
    английского «The » — нормальный ответ на русском так не начинается, полезный
    текст не теряется. Если после строки ничего не осталось — не трогаем.
    """
    body = text.lstrip()
    first, _, rest = body.partition("\n")
    if rest.strip() and re.match(r"(?i)^the\b", first):
        return rest.strip()
    return text


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


async def _answer_owner_query_api(
    question: str, context_text: str, history: Sequence[Any] | None = None
) -> str:
    client = _get_client()
    # api-бэкенд (платный ключ, без MCP-инструментов) — многоходовость нативной
    # messages-лентой: прежние реплики как есть, снимок и вопрос — в последней.
    messages: list[dict] = [
        {"role": getattr(t, "role", "user"), "content": getattr(t, "text", "")}
        for t in (history or [])
    ]
    messages.append(
        {
            "role": "user",
            "content": (
                f"Данные автопарка:\n{context_text}\n\n"
                f"Вопрос владельца: {question}"
            ),
        }
    )
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=_OWNER_SYSTEM,
        messages=messages,
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


def _cli_owner_prompt(
    question: str, context_text: str, history: Sequence[Any] | None = None
) -> str:
    prior = _history_lines(history)
    convo = ("\n\nПредыдущие реплики разговора:\n" + "\n".join(prior)) if prior else ""
    return (
        f"{_OWNER_SYSTEM}\n\n"
        f"Данные автопарка:\n{context_text}"
        f"{convo}\n\n"
        f"Вопрос владельца: {question}\n\n"
        "Ответь текстом для пользователя, не используй инструменты."
    )


async def _answer_owner_query_cli(
    question: str, context_text: str, history: Sequence[Any] | None = None
) -> str:
    args = [
        "-p",
        _cli_owner_prompt(question, context_text, history),
        "--output-format",
        "json",
        "--model",
        settings.assistant_model,
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


def _tools_enabled() -> bool:
    return bool(
        settings.assistant_use_tools and settings.mcp_url and settings.mcp_token
    )


def _tools_payload(question: str, history: Sequence[Any] | None = None) -> dict:
    return {
        # История текущего разговора вшивается в сам prompt: шлюз /v1/prompt
        # принимает одну строку, своей памяти сессий не держит.
        "prompt": _compose_owner_prompt(question, history),
        "model": settings.assistant_model,
        "system_prompt": _owner_tools_system(),
        "mcp_servers": {
            "mcpServers": {
                "fleet": {
                    "type": "http",
                    "url": settings.mcp_url,
                    "headers": {"Authorization": f"Bearer {settings.mcp_token}"},
                }
            }
        },
        "allowed_tools": ["mcp__fleet"],
    }


async def _answer_via_tools(
    question: str, history: Sequence[Any] | None = None
) -> str | None:
    """ИИ сам дёргает MCP-сервер fleet под вопрос. None — пустой ответ (на фолбэк)."""
    body = await _gateway_post("/v1/prompt", _tools_payload(question, history))
    text = str(body.get("text") or "").strip()
    if not _is_answer(_strip_meta_preamble(text)):
        # Пусто или одна служебная строка без ответа («The user's question… No
        # tools needed.»): владельцу такое не показываем — уходим в фолбэк.
        logger.warning("ассистент (инструменты): пустой или служебный ответ — фолбэк")
        return None
    return text


async def _answer_owner_query_http(
    question: str, context_text: str, history: Sequence[Any] | None = None
) -> str:
    # Режим инструментов: снимок в промпт не шлём. При сбое/пустом ответе —
    # фолбэк на старый снимок-промпт (флаг ASSISTANT_USE_TOOLS — постоянный откат).
    if _tools_enabled():
        try:
            text = await _answer_via_tools(question, history)
            if text:
                return text
        except Exception:  # noqa: BLE001 — падать на фолбэк, а не ронять ответ
            logger.warning("ассистент на инструментах упал — фолбэк на снимок", exc_info=True)

    body = await _gateway_post(
        "/v1/prompt",
        {
            "prompt": _cli_owner_prompt(question, context_text, history),
            "model": settings.assistant_model,
        },
    )
    text = str(body.get("text") or "").strip()
    return text if _is_answer(_strip_meta_preamble(text)) else _NO_ANSWER
