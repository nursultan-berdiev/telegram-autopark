"""UI-рендер, который не должен считать деньги/логику сам.

Два независимых куска:
- `_due_line` (schedules.py) — рисует состояние платежа по словарю `status`,
  который целиком считает core-api (schedule_status/due_summary).
- `INVITE_PROBLEM_TEXT` (registration.py) — тексты отказа по ссылке-инвайту,
  должны покрывать все причины, которые реально возвращает core-api.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.handlers.schedules import _due_line

# ---------------------------------------------------------------- _due_line


def _status(**over) -> dict:
    base = {
        "next_due_date": datetime(2026, 8, 20, tzinfo=timezone.utc).isoformat(),
        "amount": "1500.00",
        "paid_in_period": "0.00",
        "remaining_current": "1500.00",
        "overdue_periods": 0,
        "overdue_days": 0,
        "debt_now": "0.00",
        "is_overdue": False,
        "summary": "",
        "period_label": "ежедневно",
    }
    base.update(over)
    return base


def test_due_line_not_overdue_shows_next_payment():
    line = _due_line(_status())
    assert "Следующий платёж: 20.08.2026" in line
    assert "просрочен" not in line.lower()


def test_due_line_due_today_has_no_word_overdue():
    """overdue_days == 0 — срок наступил сегодня, это ещё не просрочка."""
    status = _status(is_overdue=True, overdue_days=0, debt_now="1500.00")
    line = _due_line(status)
    assert "Срок сегодня" in line
    assert "просрочен" not in line.lower()
    assert "1500.00" in line


def test_due_line_overdue_shows_days_and_debt():
    status = _status(is_overdue=True, overdue_days=20, debt_now="3000.00")
    line = _due_line(status)
    assert "просрочен на 20 дн." in line
    assert "3000.00" in line


# ------------------------------------------------------- INVITE_PROBLEM_TEXT

# Причины отказа, которые реально возвращает core-api (см.
# services/core-api/app/domain/invitations.py::InviteProblem). Прямой импорт
# оттуда невозможен: у core-api и у бота корневой пакет одинаково называется
# "app", и в процессе тестов бота уже загружен app бота — импорт подставит
# не тот модуль. Поэтому значения продублированы явно.
CORE_API_INVITE_PROBLEMS = ["not_found", "expired", "used", "car_taken"]


def test_invite_problem_text_covers_all_core_api_reasons():
    try:
        from app.handlers.registration import INVITE_PROBLEM_TEXT
    except ImportError as exc:  # pragma: no cover - файл не в моей зоне владения
        import pytest

        pytest.fail(
            "app.handlers.registration не импортируется — файл владеет другой "
            f"агент, самостоятельно не чиню: {exc}"
        )

    keys = {getattr(key, "value", key) for key in INVITE_PROBLEM_TEXT}
    missing = set(CORE_API_INVITE_PROBLEMS) - keys
    assert not missing, f"нет текста для причин отказа инвайта: {sorted(missing)}"

    for key, text in INVITE_PROBLEM_TEXT.items():
        assert text and text.strip(), f"пустой текст для причины {key!r}"


def test_receipt_text_mentions_prepayment():
    """Переплата переносится в следующий период — водитель должен это видеть."""
    from app.handlers.payments import _driver_receipt_text

    text = _driver_receipt_text(
        {
            "payment": {"amount": "5000.00"},
            "next_due_date": "2026-09-15T00:00:00+00:00",
            "periods_closed": 2,
            "paid_in_period": "1500.00",
        }
    )

    assert "Закрыто периодов: 2" in text
    assert "предоплата" in text
    assert "1500.00" in text


def test_receipt_text_without_prepayment_is_silent():
    from app.handlers.payments import _driver_receipt_text

    text = _driver_receipt_text(
        {
            "payment": {"amount": "1000.00"},
            "next_due_date": "2026-09-15T00:00:00+00:00",
            "periods_closed": 1,
            "paid_in_period": "0",
        }
    )

    assert "предоплата" not in text


def test_upcoming_report_counts_overdue_from_next_day():
    """«Срок сегодня» ≠ «просрочка»: в счётчик и в сумму долга он не попадает."""
    from app.handlers.reports import _upcoming_text

    rows = [
        {
            "driver_name": "Сегодня",
            "plate": "AA",
            "debt_now": "1000.00",
            "is_overdue": True,
            "overdue_days": 0,
            "summary": "срок сегодня, к оплате 1000.00",
        },
        {
            "driver_name": "Должник",
            "plate": "BB",
            "debt_now": "3000.00",
            "is_overdue": True,
            "overdue_days": 20,
            "summary": "просрочен 20 дн., долг 3000.00",
        },
    ]

    text = _upcoming_text(rows)

    assert "В просрочке: 1" in text
    assert "3000.00" in text and "4000.00" not in text
    assert "⚠️ Должник" in text
    assert "⚠️ Сегодня" not in text


async def test_schedule_amount_rejects_garbage_without_crash():
    """Регрессия: без импорта InvalidOperation этот путь падал NameError."""
    from app.handlers.schedules import set_amount

    answers: list[str] = []

    class _Msg:
        text = "не число"

        async def answer(self, text: str, **kwargs) -> None:
            answers.append(text)

    class _State:
        async def set_state(self, *a, **k) -> None: ...
        async def update_data(self, **k) -> None: ...
        async def get_data(self) -> dict:
            return {}

    await set_amount(_Msg(), _State())

    assert "Введите сумму числом" in answers[0]
