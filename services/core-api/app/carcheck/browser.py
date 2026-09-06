"""Проверка номера на carcheck.gov.kg через настоящий браузер.

Публичного API у сервиса нет, а токен reCAPTCHA v3 выпускается только в
контексте страницы — обычный HTTP-клиент получает отказ. Поэтому страница
открывается браузером и ответ забирается перехватом её собственного запроса:
разбирать вёрстку хрупче, JSON стабильнее.

Оценка reCAPTCHA не гарантирована: тот же прогон в разные дни и проходит, и
получает CAPTCHA_LOW_SCORE. Отказ обязан отличаться от «штрафов нет», иначе
заблокированная проверка выглядит как успешная и пустая.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:  # реальный импорт playwright нужен только внутри open_session
    from playwright.sync_api import ElementHandle, Page, Response

log = logging.getLogger(__name__)

BASE_URL = "https://carcheck.gov.kg/ru"
ENDPOINT = "/violation-check/find-by-plate"
PLATE_INPUT = "input[name='govPlate']"
# У сайта переключатель Кырг/Рус/Eng, и «check» как подстрока встречается в
# посторонних кнопках — поэтому список точных подписей.
SUBMIT_LABELS = (
    "проверить",
    "проверить штрафы",
    "текшер",
    "текшерүү",
    "check",
    "check fines",
)
PAGE_TIMEOUT_MS = 45_000
RESPONSE_TIMEOUT_MS = 25_000


@dataclass(frozen=True)
class CheckResult:
    """Исход проверки одного номера.

    `refused` отделён от неуспеха намеренно: сервис ответил и отказал —
    это не сбой сети и не отсутствие штрафов.
    """

    plate: str
    payload: dict[str, object] | None = None
    refused: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.payload is not None


def _find_submit(page: "Page") -> "ElementHandle | None":
    field = page.query_selector(PLATE_INPUT)
    scope = None
    if field is not None:
        scope = field.evaluate_handle("el => el.closest('form')").as_element()
    for root in (scope, page):
        if root is None:
            continue
        for button in root.query_selector_all("button"):
            label = (button.inner_text() or "").strip().lower()
            if label in SUBMIT_LABELS and not button.is_disabled():
                return button
    return None


class CarcheckSession:
    """Один браузер на весь прогон парка: перезапуск на каждый номер долог."""

    def __init__(self, page: "Page") -> None:
        self._page = page

    def check(self, plate: str) -> CheckResult:
        page = self._page
        try:
            page.goto(BASE_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
            page.wait_for_selector(PLATE_INPUT, timeout=PAGE_TIMEOUT_MS)
            field = page.query_selector(PLATE_INPUT)
            field.click()
            field.fill("")
            field.type(plate, delay=90)

            button = _find_submit(page)
            if button is None:
                return CheckResult(plate, error="кнопка проверки не найдена")

            # Ответ привязан к вызвавшему его клику, а не к времени прихода:
            # задержавшийся ответ предыдущего номера иначе приписал бы штрафы
            # не той машине.
            with page.expect_response(
                lambda r: ENDPOINT in r.url, timeout=RESPONSE_TIMEOUT_MS
            ) as caught:
                button.click()
            response: "Response" = caught.value
            status, body = response.status, response.text()
        except Exception as exc:  # noqa: BLE001 — падение одного номера не должно рушить прогон
            log.warning("проверка номера %s не удалась", plate, exc_info=True)
            return CheckResult(plate, error=f"{type(exc).__name__}: {exc}")

        try:
            payload = json.loads(body)
        except ValueError:
            log.warning("ответ по номеру %s не разобран как JSON: %.200s", plate, body)
            return CheckResult(plate, error="ответ сервиса не разобран как JSON")
        if status != 200:
            reason = payload.get("code") or payload.get("message") or f"HTTP {status}"
            return CheckResult(plate, refused=str(reason))
        return CheckResult(plate, payload=payload)


@contextmanager
def open_session() -> Iterator[CarcheckSession]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            locale="ru-RU",
            timezone_id="Asia/Bishkek",
            viewport={"width": 1440, "height": 900},
        )
        try:
            yield CarcheckSession(context.new_page())
        finally:
            browser.close()
