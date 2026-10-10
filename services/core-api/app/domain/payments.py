"""Операции с платежами и защита от повторной отправки чека."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Car, Driver, Payment, PaymentStatus
from app.clients.ai_gateway import RecognizedReceipt


def receipt_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


async def is_duplicate(session: AsyncSession, rhash: str) -> bool:
    """Проверяет, был ли уже принят чек с таким содержимым (любой водитель)."""
    existing = await session.scalar(
        select(Payment.id).where(Payment.receipt_hash == rhash)
    )
    return existing is not None


async def create_payment(
    session: AsyncSession,
    *,
    driver_id: int,
    car_id: int | None,
    amount: float,
    paid_at: datetime | None,
    receipt_file_id: str | None,
    receipt_path: str | None,
    receipt_hash: str,
    recognized: RecognizedReceipt,
    receipt_kind: str = "photo",
    commit: bool = True,
) -> Payment:
    payment = Payment(
        driver_id=driver_id,
        car_id=car_id,
        amount=amount,
        paid_at=paid_at,
        receipt_file_id=receipt_file_id,
        receipt_path=receipt_path,
        receipt_hash=receipt_hash,
        receipt_kind=receipt_kind,
        recognized_data=json.dumps(
            {
                "readable": recognized.readable,
                "amount": recognized.amount,
                "currency": recognized.currency,
                "paid_at": recognized.paid_at_raw,
                "note": recognized.note,
            },
            ensure_ascii=False,
        ),
        status=PaymentStatus.confirmed,
    )
    session.add(payment)
    # commit=False — когда вызывающий кладёт платёж и график одной транзакцией.
    if commit:
        await session.commit()
    else:
        await session.flush()
    await session.refresh(payment)
    return payment


async def list_payments_by_driver(
    session: AsyncSession, driver_id: int
) -> list[Payment]:
    result = await session.scalars(
        select(Payment)
        .where(Payment.driver_id == driver_id)
        .order_by(Payment.created_at.desc())
    )
    return list(result.all())


async def list_recent(
    session: AsyncSession, *, hours: int = 24, limit: int = 50
) -> list[tuple[Payment, str | None, str | None]]:
    """Последние оплаты по всему парку: (платёж, номер машины, имя водителя).

    Для ИИ-ассистента («кто оплатил сегодня/за сутки»): по парку, с лимитом —
    в домене до этого был только помощник по одному водителю. `hours` ограничиваем
    (вход может прийти от LLM): иначе отрицательное/огромное значение даёт пустую
    выдачу или OverflowError в timedelta.
    """
    try:
        hours = max(1, min(int(hours), 720))
    except (TypeError, ValueError):
        hours = 24
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = await session.execute(
        select(Payment, Car.plate, Driver.full_name)
        .join(Car, Car.id == Payment.car_id, isouter=True)
        .join(Driver, Driver.id == Payment.driver_id, isouter=True)
        .where(Payment.created_at >= since)
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    return [(payment, plate, name) for payment, plate, name in rows.all()]


async def paid_today(
    session: AsyncSession, tz: str, *, now: datetime | None = None
) -> list[tuple[int, str | None, str | None, float]]:
    """Оплаты за текущие локальные сутки, свёрнутые по водителю.

    Возвращает (driver_id, имя, номер машины, сумма за день). Граница суток —
    по часовому поясу парка (в отличие от `list_recent`, который считает окно
    часов от now в UTC): для сводки «кто оплатил сегодня» нужен именно день.
    `now` передаётся из сводки, чтобы все её части считались от одного момента;
    по умолчанию — текущее время.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now_local = now.astimezone(ZoneInfo(tz))
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight_local.astimezone(timezone.utc)
    rows = await session.execute(
        select(
            Driver.id,
            Driver.full_name,
            Car.plate,
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .join(Driver, Driver.id == Payment.driver_id)
        .join(Car, Car.id == Payment.car_id, isouter=True)
        .where(
            Payment.created_at >= since,
            Payment.status == PaymentStatus.confirmed,
        )
        .group_by(Driver.id, Driver.full_name, Car.plate)
        .order_by(Driver.full_name)
    )
    return [(did, name, plate, float(total)) for did, name, plate, total in rows.all()]
