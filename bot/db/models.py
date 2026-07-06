"""ORM-модели предметной области.

Схема покрывает весь Этап 1: автомобили, водители, приглашения,
графики платежей и платежи. Отдельные модули этапов работают с этими
таблицами по мере реализации.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


class CarStatus(str, enum.Enum):
    free = "free"
    occupied = "occupied"


class InviteStatus(str, enum.Enum):
    active = "active"
    used = "used"
    expired = "expired"


class SchedulePeriod(str, enum.Enum):
    """Тип периодичности графика (гибкая настройка — FR-SCH-3)."""

    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    custom = "custom"  # произвольный интервал в днях (см. interval_days)


class PaymentStatus(str, enum.Enum):
    confirmed = "confirmed"


class Car(Base):
    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plate: Mapped[str] = mapped_column(String(32), unique=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[CarStatus] = mapped_column(
        Enum(CarStatus, name="car_status"), default=CarStatus.free
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    driver: Mapped["Driver | None"] = relationship(
        back_populates="car", uselist=False
    )


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(256))
    phone: Mapped[str] = mapped_column(String(32))
    inn: Mapped[str] = mapped_column(String(32))
    selfie_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    selfie_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    car_id: Mapped[int | None] = mapped_column(
        ForeignKey("cars.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    car: Mapped["Car | None"] = relationship(back_populates="driver")
    schedule: Mapped["PaymentSchedule | None"] = relationship(
        back_populates="driver", uselist=False
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="driver")


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"))
    created_by: Mapped[int] = mapped_column(BigInteger)  # tg id админа
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, name="invite_status"), default=InviteStatus.active
    )
    used_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    car: Mapped["Car"] = relationship()


class PaymentSchedule(Base):
    __tablename__ = "payment_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(
        ForeignKey("drivers.id", ondelete="CASCADE"), unique=True
    )
    period: Mapped[SchedulePeriod] = mapped_column(
        Enum(SchedulePeriod, name="schedule_period")
    )
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    next_due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    driver: Mapped["Driver"] = relationship(back_populates="schedule")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id", ondelete="CASCADE"))
    car_id: Mapped[int | None] = mapped_column(
        ForeignKey("cars.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # дата/время из чека (распознано ИИ)
    receipt_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    receipt_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    receipt_hash: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )  # для защиты от повторной отправки одного чека
    recognized_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.confirmed
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    driver: Mapped["Driver"] = relationship(back_populates="payments")
