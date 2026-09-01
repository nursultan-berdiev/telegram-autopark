"""Операции с платежами и защита от повторной отправки чека."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, PaymentStatus
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
