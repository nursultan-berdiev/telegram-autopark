"""ORM-модели предметной области.

Схема покрывает весь Этап 1: автомобили, водители, приглашения,
графики платежей и платежи. Отдельные модули этапов работают с этими
таблицами по мере реализации.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


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
    __table_args__ = (
        # Гонка двух регистраций по одному инвайту иначе сажает двух водителей
        # в одну машину: проверка «свободна» — это check-then-act.
        Index(
            "uq_driver_active_car",
            "car_id",
            unique=True,
            postgresql_where=text("active AND car_id IS NOT NULL"),
            sqlite_where=text("active = 1 AND car_id IS NOT NULL"),
        ),
    )

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
    # Увольнение — это АРХИВИРОВАНИЕ, а не удаление: запись и вся история
    # платежей сохраняются (payments.driver_id стоит на CASCADE, удаление
    # водителя стёрло бы его выписку). Уволенный теряет доступ к боту,
    # машина освобождается, график останавливается.
    active: Mapped[bool] = mapped_column(default=True, server_default="true")
    fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    # Внесено в счёт ТЕКУЩЕГО (ещё не закрытого) периода — частичная оплата.
    # Инвариант: 0 <= paid_in_period < amount (переплата сразу закрывает периоды).
    paid_in_period: Mapped[float] = mapped_column(
        Numeric(12, 2), default=0, server_default="0"
    )
    next_due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(default=True)
    # Локальная дата последнего напоминания — не чаще одного в день,
    # иначе при просрочке в 40 дней водитель получит 40 сообщений.
    last_reminded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
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
    # "photo" | "document" — чем чек пришёл в Telegram. Пересылать владельцу
    # file_id документа через send_photo нельзя (Telegram отвергнет), а чек
    # приходит и фотографией, и файлом (скриншот, PDF из банка).
    receipt_kind: Mapped[str] = mapped_column(
        String(16), default="photo", server_default="photo"
    )
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


# --- Трекеры и телеметрия (миграция 0006) ---------------------------------


class TrackerProvider(str, enum.Enum):
    traccar = "traccar"


class Tracker(Base):
    __tablename__ = "trackers"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_tracker_provider_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[TrackerProvider] = mapped_column(
        Enum(TrackerProvider, name="tracker_provider"),
        default=TrackerProvider.traccar,
    )
    external_id: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (Index("ix_telemetry_car_ts", "car_id", "ts"),)

    # BIGINT в SQLite не алиас rowid — автоинкремент в тестах без варианта не работает.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"))
    tracker_id: Mapped[int | None] = mapped_column(
        ForeignKey("trackers.id", ondelete="SET NULL"), nullable=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    server_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_knots: Mapped[float | None] = mapped_column(Float, nullable=True)
    course: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid: Mapped[bool] = mapped_column(default=True)
    ignition: Mapped[bool | None] = mapped_column(nullable=True)
    motion: Mapped[bool | None] = mapped_column(nullable=True)
    total_distance_km: Mapped[float | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    engine_blocked: Mapped[bool | None] = mapped_column(nullable=True)
    status_raw: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CarState(Base):
    """Последний снимок машины.

    online здесь НЕ хранится: колонка не протухает сама, и гейт блокировки
    считал бы офлайн-машину живой (plan/02).
    """

    __tablename__ = "car_state"

    car_id: Mapped[int] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), primary_key=True
    )
    tracker_id: Mapped[int | None] = mapped_column(
        ForeignKey("trackers.id", ondelete="SET NULL"), nullable=True
    )
    last_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_knots: Mapped[float | None] = mapped_column(Float, nullable=True)
    ignition: Mapped[bool | None] = mapped_column(nullable=True)
    motion: Mapped[bool | None] = mapped_column(nullable=True)
    odometer_km: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    # Пробег принадлежит ТРЕКЕРУ, а не машине: смена устройства обнуляет счётчик.
    odometer_tracker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    odometer_trusted: Mapped[bool] = mapped_column(default=True, server_default="1")
    engine_blocked: Mapped[bool] = mapped_column(default=False, server_default="0")
    last_command: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# --- Штрафы и обслуживание (миграция 0007) --------------------------------


class FineStatus(str, enum.Enum):
    unpaid = "unpaid"
    paid = "paid"


class Fine(Base):
    __tablename__ = "fines"
    __table_args__ = (Index("ix_fines_car_issued", "car_id", "issued_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int] = mapped_column(ForeignKey("cars.id", ondelete="CASCADE"))
    driver_id: Mapped[int | None] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Без статуса правило «N штрафов» не смогло бы закрыться никогда.
    status: Mapped[FineStatus] = mapped_column(
        Enum(FineStatus, name="fine_status"), default=FineStatus.unpaid
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), default="manual", server_default="manual")
    external_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MaintenanceItem(Base):
    __tablename__ = "maintenance_items"
    __table_args__ = (
        UniqueConstraint("car_id", "type", name="uq_maintenance_car_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(32))
    interval_km: Mapped[float] = mapped_column(Numeric(12, 3))
    last_service_km: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    # От какого трекера снята база: иначе смену устройства не поймать.
    last_service_tracker_id: Mapped[int | None] = mapped_column(
        ForeignKey("trackers.id", ondelete="SET NULL"), nullable=True
    )
    last_service_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- Правила, алерты, команды (миграция 0008) -----------------------------


class RuleType(str, enum.Enum):
    overdue_payment = "overdue_payment"
    fines_count = "fines_count"
    maintenance_km = "maintenance_km"


class AlertType(str, enum.Enum):
    overdue_payment = "overdue_payment"
    fines_count = "fines_count"
    maintenance_km = "maintenance_km"
    # Системные: рождаются джобами/обработчиками, rule_id=NULL.
    command_unconfirmed = "command_unconfirmed"
    odometer_untrusted = "odometer_untrusted"


class AlertStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"


class CommandType(str, enum.Enum):
    engine_stop = "engine_stop"
    engine_resume = "engine_resume"
    alarm_arm = "alarm_arm"
    alarm_disarm = "alarm_disarm"


class CommandStatus(str, enum.Enum):
    queued = "queued"
    blocked_by_safety = "blocked_by_safety"
    sent = "sent"
    acked = "acked"
    unconfirmed = "unconfirmed"
    failed = "failed"


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int | None] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), nullable=True
    )  # NULL — правило на все машины
    type: Mapped[RuleType] = mapped_column(Enum(RuleType, name="rule_type"))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning", server_default="warning")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Alert(Base):
    """Срабатывание правила или системное событие (rule_id=NULL)."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alert_open_rule",
            "rule_id",
            "car_id",
            unique=True,
            sqlite_where=text("status = 'open' AND rule_id IS NOT NULL"),
            postgresql_where=text("status = 'open' AND rule_id IS NOT NULL"),
        ),
        Index(
            "uq_alert_open_sys",
            "car_id",
            "type",
            unique=True,
            sqlite_where=text("status = 'open' AND rule_id IS NULL"),
            postgresql_where=text("status = 'open' AND rule_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rules.id", ondelete="CASCADE"), nullable=True
    )
    car_id: Mapped[int] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[AlertType] = mapped_column(Enum(AlertType, name="alert_type"))
    severity: Mapped[str] = mapped_column(String(16), default="warning", server_default="warning")
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status"), default=AlertStatus.open, index=True
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )  # момент ПЕРВОГО срабатывания: при повторе не трогаем
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    action_taken: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Command(Base):
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    car_id: Mapped[int] = mapped_column(
        ForeignKey("cars.id", ondelete="CASCADE"), index=True
    )
    tracker_id: Mapped[int | None] = mapped_column(
        ForeignKey("trackers.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[CommandType] = mapped_column(Enum(CommandType, name="command_type"))
    status: Mapped[CommandStatus] = mapped_column(
        Enum(CommandStatus, name="command_status"), default=CommandStatus.queued
    )
    requested_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )
    safety_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
