"""Авто-блокировка за неоплаченные штрафы: новые типы системных алертов.

`fines_block_fired` — авто-блокировка за превышение лимита неоплаченных штрафов
сработала; `fines_unblock` — штрафы погашены, двигатель разблокирован;
`fines_unblock_failed` — авто-разблокировку не удалось отправить. Все — аддитивные
значения native-enum PostgreSQL; на SQLite enum хранится строкой, добавлять нечего.
Новый источник команды `fines` хранится в commands.source (String) — миграции не
требует. Новых колонок/таблиц нет.

Revision ID: 0020_fines_block_alerts
Revises: 0019_overdue_enforcement_alerts
Create Date: 2026-10-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020_fines_block_alerts"
down_revision: Union[str, None] = "0019_overdue_enforcement_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ADD VALUE нельзя в той же транзакции, где значение используется —
    # отдельным автокоммитом (как в 0012/0017/0019).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'fines_block_fired'")
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'fines_unblock'")
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'fines_unblock_failed'")


def downgrade() -> None:
    # PostgreSQL не умеет удалять значение из enum; значения безвредны.
    pass
