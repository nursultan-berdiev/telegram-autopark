"""Напоминания: маркер последнего напоминания по графику.

Добавляет payment_schedules.last_reminded_on — локальная дата, когда водителю
последний раз отправили напоминание. Нужен, чтобы не слать больше одного
сообщения в день (иначе просрочка в 40 дней = 40 сообщений).

Revision ID: 0003_schedule_remind
Revises: 0002_schedule_paid
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_schedule_remind"
down_revision: Union[str, None] = "0002_schedule_paid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_schedules",
        sa.Column("last_reminded_on", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_schedules", "last_reminded_on")
