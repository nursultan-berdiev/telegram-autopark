"""Клиент правового портала «Төлөм» (tolom.kg).

В отличие от carcheck браузер не нужен: под SPA лежит открытый JSON-API, и
страница обращается к нему обычным POST. Форма портала кладёт в тело токен
reCAPTCHA, но сервер его не проверяет — сам сайт для QR штатно шлёт
`token: null`. Мы шлём ровно то, что шлёт форма, и ничего не обходим; если
проверку однажды включат, отказ придёт как `refused` и будет виден оператору,
а не притворится «штрафов нет».

WAF портала режет клиентов по умолчанию (`python-httpx/…` → 403), поэтому
представляемся своим именем: подделывать браузерный User-Agent не требуется —
честный проходит.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import httpx

from app.config import settings
from app.fines_sources import CheckResult
from app.tolom.parser import plate_registered

log = logging.getLogger(__name__)

ENDPOINT = "/penalty/by-plate"
USER_AGENT = "TelegramAutopark/1.0 (+fleet owner fines check)"

# Отказ относится к нам, а не к номеру: перебор продолжать нельзя.
REFUSAL_CODES = (403, 429)


class TolomSession:
    """Одна HTTP-сессия на прогон: соединение переиспользуется между номерами."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def check(self, plate: str) -> CheckResult:
        try:
            response = self._client.post(
                ENDPOINT, json={"plate": plate, "token": ""}
            )
        except httpx.HTTPError as exc:
            return CheckResult(plate, error=f"{type(exc).__name__}: {exc}")

        if response.status_code in REFUSAL_CODES:
            return CheckResult(
                plate, refused=f"сервис отклонил запрос (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            return CheckResult(plate, error=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return CheckResult(plate, error="ответ сервиса не разобран как JSON")
        if not isinstance(payload, dict):
            return CheckResult(plate, error="ответ сервиса не является объектом")
        return CheckResult(
            plate, payload=payload, plate_known=plate_registered(payload)
        )


@contextmanager
def open_session() -> Iterator[TolomSession]:
    with httpx.Client(
        base_url=settings.tolom_url,
        timeout=settings.tolom_timeout_seconds,
        headers={
            "User-Agent": USER_AGENT,
            "Content-type": "application/json",
            "Accept": "application/json",
        },
    ) as client:
        yield TolomSession(client)
