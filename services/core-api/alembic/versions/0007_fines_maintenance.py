"""Штрафы и обслуживание по пробегу — данные для правил v1.

У штрафа есть статус оплаты: без него правило «N штрафов» не смогло бы
закрыться никогда и алерт висел бы вечно. У ТО хранится трекер, с которого
снята база пробега: пробег принадлежит устройству, а не машине (plan/02).

Revision ID: 0007_fines_maintenance
Revises: 0006_tracking
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_fines_maintenance"
down_revision: Union[str, None] = "0006_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "driver_id",
            sa.Integer(),
            sa.ForeignKey("drivers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("unpaid", "paid", name="fine_status"),
            nullable=False,
            server_default="unpaid",
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source", sa.String(length=32), nullable=False, server_default="manual"
        ),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("ix_fines_car_issued", "fines", ["car_id", "issued_at"])

    op.create_table(
        "maintenance_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("interval_km", sa.Numeric(12, 3), nullable=False),
        sa.Column(
            "last_service_km", sa.Numeric(12, 3), nullable=False, server_default="0"
        ),
        sa.Column(
            "last_service_tracker_id",
            sa.Integer(),
            sa.ForeignKey("trackers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_service_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint("car_id", "type", name="uq_maintenance_car_type"),
    )
    op.create_index("ix_maintenance_items_car_id", "maintenance_items", ["car_id"])


def downgrade() -> None:
    op.drop_index("ix_maintenance_items_car_id", table_name="maintenance_items")
    op.drop_table("maintenance_items")
    op.drop_index("ix_fines_car_issued", table_name="fines")
    op.drop_table("fines")
    sa.Enum(name="fine_status").drop(op.get_bind(), checkfirst=True)
