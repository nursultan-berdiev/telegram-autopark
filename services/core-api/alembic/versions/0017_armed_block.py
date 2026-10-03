"""Взведённая (ожидающая) блокировка двигателя.

Новый статус команды `armed` (блокировка ждёт остановки машины) и новые типы
системных алертов `armed_block_fired` (взвод сработал) и `armed_block_failed`
(взвод не удалось отправить на реле). Все — аддитивные значения native-enum
PostgreSQL; на SQLite enum хранится строкой, добавлять нечего.

Revision ID: 0017_armed_block
Revises: 0016_one_running_task
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017_armed_block"
down_revision: Union[str, None] = "0016_one_running_task"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ADD VALUE нельзя в той же транзакции, где значение используется —
    # отдельным автокоммитом (как в 0012).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE command_status ADD VALUE IF NOT EXISTS 'armed'")
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'armed_block_fired'")
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'armed_block_failed'")


def downgrade() -> None:
    # PostgreSQL не умеет удалять значение из enum; значения безвредны.
    pass
