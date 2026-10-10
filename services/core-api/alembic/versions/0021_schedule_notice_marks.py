"""Антиспам вечерних уведомлений у срока: даты последней отправки по графику.

`last_warned_on` — предупреждение «за 15 минут до блокировки»; `last_block_notice_on`
— сообщение «блокировка включена» в 22:00. Обе — локальная дата (как
`last_reminded_on`), чтобы перезапуск бота около срока не повторил рассылку.
Nullable, без бэкфилла: пустое = «сегодня ещё не слали».

Revision ID: 0021_schedule_notice_marks
Revises: 0020_fines_block_alerts
Create Date: 2026-10-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0021_schedule_notice_marks"
down_revision: Union[str, None] = "0020_fines_block_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_schedules",
        sa.Column("last_warned_on", sa.Date(), nullable=True),
    )
    op.add_column(
        "payment_schedules",
        sa.Column("last_block_notice_on", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_schedules", "last_block_notice_on")
    op.drop_column("payment_schedules", "last_warned_on")
