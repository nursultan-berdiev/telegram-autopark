"""Начальная схема: cars, drivers, invitations, payment_schedules, payments.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plate", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("photo_file_id", sa.String(length=256), nullable=True),
        sa.Column("photo_path", sa.String(length=512), nullable=True),
        sa.Column(
            "status",
            sa.Enum("free", "occupied", name="car_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plate"),
    )

    op.create_table(
        "drivers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=256), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("inn", sa.String(length=32), nullable=False),
        sa.Column("selfie_file_id", sa.String(length=256), nullable=True),
        sa.Column("selfie_path", sa.String(length=512), nullable=True),
        sa.Column("car_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tg_user_id"),
    )
    op.create_index("ix_drivers_tg_user_id", "drivers", ["tg_user_id"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "used", "expired", name="invite_status"),
            nullable=False,
        ),
        sa.Column("used_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_invitations_code", "invitations", ["code"])

    op.create_table(
        "payment_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column(
            "period",
            sa.Enum("daily", "weekly", "monthly", "custom", name="schedule_period"),
            nullable=False,
        ),
        sa.Column("interval_days", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("next_due_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("driver_id"),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("car_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_file_id", sa.String(length=256), nullable=True),
        sa.Column("receipt_path", sa.String(length=512), nullable=True),
        sa.Column("receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("recognized_data", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("confirmed", name="payment_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["car_id"], ["cars.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payments_receipt_hash", "payments", ["receipt_hash"])


def downgrade() -> None:
    op.drop_index("ix_payments_receipt_hash", table_name="payments")
    op.drop_table("payments")
    op.drop_table("payment_schedules")
    op.drop_index("ix_invitations_code", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_drivers_tg_user_id", table_name="drivers")
    op.drop_table("drivers")
    op.drop_table("cars")
    sa.Enum(name="payment_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="schedule_period").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="invite_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="car_status").drop(op.get_bind(), checkfirst=True)
