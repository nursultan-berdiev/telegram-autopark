"""DTO платежей и распознавания чеков."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .common import DTO


class PaymentDTO(DTO):
    id: int
    driver_id: int
    car_id: int | None = None
    amount: Decimal
    paid_at: datetime | None = None
    receipt_file_id: str | None = None
    receipt_path: str | None = None
    receipt_kind: str = "photo"
    status: str = "confirmed"
    created_at: datetime | None = None


class RecognizedReceiptDTO(DTO):
    # Нечитаемый чек и чек без суммы — разные случаи: первый просят переснять.
    readable: bool = True
    amount: Decimal | None = None
    currency: str | None = None
    paid_at: datetime | None = None
    note: str | None = None
    raw_text: str | None = None


class PaymentCreate(DTO):
    driver_id: int
    amount: Decimal
    paid_at: datetime | None = None
    receipt_file_id: str | None = None
    receipt_path: str | None = None
    receipt_kind: str = "photo"
    receipt_hash: str | None = None
    recognized: RecognizedReceiptDTO | None = None


class PaymentResult(DTO):
    """Итог платежа: сам платёж плюс что стало с графиком."""

    payment: PaymentDTO
    periods_closed: int = 0
    paid_in_period: Decimal = Decimal("0.00")
    remaining_current: Decimal = Decimal("0.00")
    next_due_date: datetime | None = None
    duplicate: bool = False
