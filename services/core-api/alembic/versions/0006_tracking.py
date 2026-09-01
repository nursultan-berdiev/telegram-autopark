"""Трекеры, телеметрия и снимок состояния машины.

Точка расширяемости платформы: trackers связывает машину с устройством
конкретного провайдера, telemetry хранит временной ряд, car_state — быстрый
«сейчас». Поле online намеренно НЕ хранится: оно не протухает само и гейт
блокировки считал бы офлайн-машину живой (см. plan/02).

Revision ID: 0006_tracking
Revises: 0005_receipt_kind
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_tracking"
down_revision: Union[str, None] = "0005_receipt_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trackers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.Enum("traccar", name="tracker_provider"),
            nullable=False,
            server_default="traccar",
        ),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_tracker_provider_external"
        ),
    )
    op.create_index("ix_trackers_car_id", "trackers", ["car_id"])

    op.create_table(
        "telemetry",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tracker_id",
            sa.Integer(),
            sa.ForeignKey("trackers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("speed_knots", sa.Float(), nullable=True),
        sa.Column("course", sa.Float(), nullable=True),
        sa.Column("altitude", sa.Float(), nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ignition", sa.Boolean(), nullable=True),
        sa.Column("motion", sa.Boolean(), nullable=True),
        sa.Column("total_distance_km", sa.Numeric(12, 3), nullable=True),
        sa.Column("engine_blocked", sa.Boolean(), nullable=True),
        sa.Column("status_raw", sa.String(length=16), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=True),
    )
    op.create_index("ix_telemetry_car_ts", "telemetry", ["car_id", "ts"])

    op.create_table(
        "car_state",
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tracker_id",
            sa.Integer(),
            sa.ForeignKey("trackers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("speed_knots", sa.Float(), nullable=True),
        sa.Column("ignition", sa.Boolean(), nullable=True),
        sa.Column("motion", sa.Boolean(), nullable=True),
        sa.Column("odometer_km", sa.Numeric(12, 3), nullable=True),
        sa.Column("odometer_tracker_id", sa.Integer(), nullable=True),
        sa.Column(
            "odometer_trusted", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "engine_blocked", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("last_command", sa.String(length=32), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("car_state")
    op.drop_index("ix_telemetry_car_ts", table_name="telemetry")
    op.drop_table("telemetry")
    op.drop_index("ix_trackers_car_id", table_name="trackers")
    op.drop_table("trackers")
    sa.Enum(name="tracker_provider").drop(op.get_bind(), checkfirst=True)
