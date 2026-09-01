"""Роутер payments: приём чеков, распознавание ИИ, применение к графику (см. plan/03)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.clients import ai_gateway
from app.clients.ai_gateway import RecognizedReceipt
from app.db.models import Payment
from app.db.session import get_session
from app.domain import drivers as drivers_service
from app.domain import payments as pay
from app.domain import schedules as sched
from app.errors import Conflict, DomainError, NotFound
from app.storage import media_type_for
from contracts import PaymentCreate, PaymentDTO, PaymentResult, RecognizedReceiptDTO

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _payment_dto(payment: Payment) -> PaymentDTO:
    return PaymentDTO(
        id=payment.id,
        driver_id=payment.driver_id,
        car_id=payment.car_id,
        amount=payment.amount,
        paid_at=_utc(payment.paid_at),
        receipt_file_id=payment.receipt_file_id,
        receipt_path=payment.receipt_path,
        receipt_kind=payment.receipt_kind,
        status=payment.status.value,
        created_at=_utc(payment.created_at),
    )


def _to_recognized(dto: RecognizedReceiptDTO | None) -> RecognizedReceipt:
    # RecognizedReceiptDTO уже без readable/note (см. libs/contracts) — readable
    # восстанавливаем по факту передачи, raw_text как ближайшее текстовое поле.
    if dto is None:
        return RecognizedReceipt(
            readable=False, amount=None, currency=None,
            paid_at=None, paid_at_raw=None, note=None,
        )
    return RecognizedReceipt(
        readable=True,
        amount=float(dto.amount) if dto.amount is not None else None,
        currency=dto.currency,
        paid_at=dto.paid_at,
        paid_at_raw=dto.paid_at.isoformat() if dto.paid_at else None,
        note=dto.raw_text,
    )


@router.post("/payments/recognize", response_model=RecognizedReceiptDTO)
async def recognize_receipt(
    file: UploadFile = File(...),
    _: str = Depends(require_core),
) -> RecognizedReceiptDTO:
    data = await file.read()
    media_type = file.content_type or media_type_for(file.filename)
    try:
        recognized = await ai_gateway.recognize_receipt(data, media_type)
    except Exception as exc:  # noqa: BLE001 — сбой ИИ не должен падать 500-кой
        raise DomainError("сервис распознавания недоступен", status_code=502) from exc
    return RecognizedReceiptDTO(
        readable=recognized.readable,
        amount=recognized.amount,
        currency=recognized.currency,
        paid_at=recognized.paid_at,
        note=recognized.note,
        raw_text=recognized.paid_at_raw,
    )


@router.post("/payments", response_model=PaymentResult)
async def create_payment(
    payload: PaymentCreate,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> PaymentResult:
    # Дубль чека проверяем до записи — is_duplicate смотрит по всем водителям.
    if payload.receipt_hash and await pay.is_duplicate(session, payload.receipt_hash):
        raise Conflict("чек уже был")

    driver = await drivers_service.get_driver(session, payload.driver_id)
    if driver is None:
        raise NotFound(f"водитель {payload.driver_id} не найден")

    payment = await pay.create_payment(
        session,
        driver_id=payload.driver_id,
        car_id=driver.car_id,
        amount=payload.amount,
        paid_at=payload.paid_at,
        receipt_file_id=payload.receipt_file_id,
        receipt_path=payload.receipt_path,
        receipt_hash=payload.receipt_hash,
        recognized=_to_recognized(payload.recognized),
        receipt_kind=payload.receipt_kind,
        commit=False,
    )

    result = PaymentResult(payment=_payment_dto(payment))

    # Применяем к графику, только если он вообще есть у водителя (домен не переписываем).
    schedule = await sched.get_schedule(session, payload.driver_id)
    if schedule is not None:
        applied = await sched.apply_payment(
            session, schedule, payload.amount, commit=False
        )
        result.periods_closed = applied.periods_closed
        result.paid_in_period = applied.paid_in_period
        result.remaining_current = applied.remaining_current
        result.next_due_date = applied.next_due_date

    await session.commit()
    await session.refresh(payment)
    result.payment = _payment_dto(payment)
    return result


@router.get("/drivers/{driver_id}/payments", response_model=list[PaymentDTO])
async def list_driver_payments(
    driver_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[PaymentDTO]:
    payments = await pay.list_payments_by_driver(session, driver_id)
    return [_payment_dto(p) for p in payments]
