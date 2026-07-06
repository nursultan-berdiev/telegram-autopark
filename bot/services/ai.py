"""Интеграция с Claude API: распознавание чеков и (Этап 6) аналитика.

Распознавание использует vision + инструмент (tool use) с принудительным
вызовом, чтобы гарантированно получить структурированные данные чека.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime

from anthropic import AsyncAnthropic

from bot.config import settings

logger = logging.getLogger(__name__)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


@dataclass
class RecognizedReceipt:
    readable: bool
    amount: float | None
    currency: str | None
    paid_at: datetime | None
    paid_at_raw: str | None
    note: str | None


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


async def recognize_receipt(image_bytes: bytes, media_type: str) -> RecognizedReceipt:
    """Распознаёт чек через Claude. Бросает исключение при ошибке API."""
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
        paid_at_raw=paid_at_raw,
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


_OWNER_SYSTEM = (
    "Ты — ассистент владельца автопарка. Отвечай кратко и по делу на русском языке, "
    "опираясь ТОЛЬКО на приведённые данные автопарка. Если данных для ответа "
    "недостаточно, честно скажи об этом. Не выдумывай цифры."
)


async def answer_owner_query(question: str, context_text: str) -> str:
    """Отвечает на свободный вопрос владельца на основе снимка данных (FR-AI-6)."""
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
