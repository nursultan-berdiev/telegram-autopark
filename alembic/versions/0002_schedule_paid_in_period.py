"""Частичная оплата: накопитель внесённого за текущий период.

Добавляет payment_schedules.paid_in_period — сколько уже внесено в счёт
текущего (ещё не закрытого) периода. Позволяет учитывать частичную оплату,
не сдвигая срок, пока не набрана полная сумма периода.

Revision ID: 0002_schedule_paid
Revises: 0001_initial
Create Date: 2026-07-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_schedule_paid"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_schedules",
        sa.Column(
            "paid_in_period",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("payment_schedules", "paid_in_period")
