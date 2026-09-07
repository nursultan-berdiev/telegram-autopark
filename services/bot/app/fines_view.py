"""Рендер штрафов: подписи кнопок, карточка, шапки списков.

Чистые функции без aiogram — их и тестируем.

Всё, что приходит от сервиса, обязано проходить через `esc`: parse_mode=HTML
включён глобально, а место нарушения приезжает с кавычками и может приехать с
угловой скобкой — тогда Telegram отвергнет всё сообщение целиком.
"""
from __future__ import annotations

import html
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.config import settings

# API отдаёт время в UTC, а нарушение произошло по местным часам: без
# перевода штраф от 14:47 показывался бы как 08:47.
LOCAL_TZ = ZoneInfo(settings.timezone)

# Ровно два столбца по три строки: столько кнопок помещается, не превращая
# сообщение в простыню. Навигация занимает ячейки наравне со штрафами.
PAGE_CELLS = 6
COLUMNS = 2


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def money(value: object) -> str | None:
    """«1 000» — как печатает суммы сам сервис. Копейки не показываем."""
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    whole = amount.quantize(Decimal(1)) if amount == amount.to_integral() else amount
    return f"{whole:,}".replace(",", " ")


def day(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    try:
        return date.fromisoformat(text[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return None


def moment(value: object) -> str | None:
    """Момент по местному времени: сервис и оператор живут в одной зоне."""
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return day(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(LOCAL_TZ)
    return parsed.strftime("%d.%m.%Y %H:%M")


def fine_ref(fine: dict) -> str:
    return fine.get("external_ref") or f"штраф #{fine['id']}"


def fine_button_label(fine: dict, *, with_plate: bool = False) -> str:
    """Подпись кнопки: сумма и во что она превратится после скидки.

    Скобки появляются, только если скидка есть и известна дата её конца.
    Дата известна лишь при вручённом постановлении: пока не вручено, отсчёт
    не начался, и выдумывать день неоткуда.
    """
    amount = money(fine.get("amount"))
    to_pay = money(fine.get("amount_to_pay"))
    head = f"{amount} сом" if amount else "сумма неизвестна"

    if fine.get("status") == "paid":
        return _with_plate(f"{head} · оплачен", fine, with_plate)
    if to_pay and amount and to_pay != amount:
        until = day(fine.get("discount_until"))
        tail = f"{amount} после {until}" if until else f"{amount} после окончания скидки"
        head = f"{to_pay} сом ({tail})"
    return _with_plate(head, fine, with_plate)


def _with_plate(label: str, fine: dict, with_plate: bool) -> str:
    plate = fine.get("car_plate")
    return f"{plate} · {label}" if with_plate and plate else label


def fine_card_text(fine: dict) -> str:
    """Карточка штрафа — целиком из нашей БД, без похода в сервис."""
    lines = [f"🧾 <b>{esc(fine_ref(fine))}</b>"]
    if fine.get("car_plate"):
        lines.append(f"Машина: {esc(fine['car_plate'])}")

    what = " — ".join(
        esc(part)
        for part in (fine.get("article"), fine.get("violation_title"))
        if part
    )
    if what:
        lines.append(f"Нарушение: {what}")
    if fine.get("place"):
        lines.append(f"Место: {esc(fine['place'])}")
    when = moment(fine.get("issued_at"))
    if when:
        lines.append(f"Когда: {when}")

    amount = money(fine.get("amount"))
    lines.append(f"Сумма: {amount} сом" if amount else "Сумма: неизвестна")
    to_pay = money(fine.get("amount_to_pay"))
    if to_pay and to_pay != amount:
        until = day(fine.get("discount_until"))
        lines.append(
            f"Со скидкой: {to_pay} сом до {until}"
            if until
            else f"Со скидкой: {to_pay} сом"
        )
    handed = day(fine.get("delivery_date"))
    lines.append(
        f"Постановление вручено: {handed}"
        if handed
        else "Постановление не вручено — срок скидки не идёт"
    )
    if fine.get("payment_code"):
        lines.append(f"Код оплаты: <code>{esc(fine['payment_code'])}</code>")

    lines.append(_status_line(fine))
    if fine.get("note"):
        lines.append(f"Примечание: {esc(fine['note'])}")
    seen = moment(fine.get("last_seen_at"))
    if seen:
        lines.append(f"Источник: {esc(fine.get('last_seen_source') or fine['source'])}, проверено {seen}")
    return "\n".join(lines)


def _status_line(fine: dict) -> str:
    if fine.get("status") != "paid":
        return "Статус: не оплачен"
    paid_at = day(fine.get("paid_at"))
    when = f" {paid_at}" if paid_at else ""
    if fine.get("paid_by") and fine["paid_by"] != "admin":
        # Отметку человека и машинную нельзя показывать одинаково: во втором
        # случае мы не видели платежа, а лишь заметили пропажу из ответа.
        return f"Статус: оплачен{when} (пропал из {esc(fine['paid_by'])})"
    return f"Статус: оплачен{when}"


def total_line(fines: list[dict]) -> str:
    """Итог по списку, честный к неизвестным суммам.

    Оплаченные не считаем: на экранах, где показан весь список машины, они
    завысили бы сумму «к оплате» на всё, что уже погашено.
    """
    unpaid = [f for f in fines if f.get("status") != "paid"]
    known = [f for f in unpaid if f.get("amount_to_pay") or f.get("amount")]
    if not known:
        return ""
    total = sum(
        Decimal(str(f.get("amount_to_pay") or f.get("amount"))) for f in known
    )
    prefix = "к оплате" if len(known) == len(unpaid) else "к оплате не менее"
    return f"{prefix} {money(total)} сом"


def paginate(total: int) -> list[tuple[int, int]]:
    """Границы страниц с учётом того, что стрелки занимают ячейки клавиатуры.

    Первая страница — 5 штрафов и «далее», средняя — «назад», 4 штрафа и
    «далее», последняя — «назад» и до 5 штрафов. Если всё влезло на одну
    страницу, стрелок нет вовсе и помещается 6.
    """
    if total <= PAGE_CELLS:
        return [(0, total)]
    pages: list[tuple[int, int]] = []
    start = 0
    while start < total:
        capacity = PAGE_CELLS - (0 if not pages else 1)
        if total - start > capacity:
            capacity -= 1
        end = min(start + capacity, total)
        pages.append((start, end))
        start = end
    return pages


def page_slice(fines: list[dict], page: int) -> tuple[list[dict], int, int]:
    """Штрафы страницы, номер страницы и их общее число."""
    pages = paginate(len(fines)) or [(0, 0)]
    index = max(0, min(page, len(pages) - 1))
    start, end = pages[index]
    return fines[start:end], index, len(pages)


def list_header(title: str, fines: list[dict], page_index: int, pages: int) -> str:
    head = f"{esc(title)}: {len(fines)}"
    total = total_line(fines)
    if total:
        head += f", {total}"
    if pages > 1:
        head += f"\nстраница {page_index + 1}/{pages}"
    return head
