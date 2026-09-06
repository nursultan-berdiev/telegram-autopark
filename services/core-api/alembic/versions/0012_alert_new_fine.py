"""Тип алерта «новый штраф».

Оператору нужен факт появления штрафа, а сумму сервис не отдаёт вовсе —
её смотрят вручную по номеру постановления.

Revision ID: 0012_alert_new_fine
Revises: 0011_periodic_tasks
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012_alert_new_fine"
down_revision: Union[str, None] = "0011_periodic_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite хранит enum строкой, добавлять нечего.
        return
    # ADD VALUE нельзя использовать в той же транзакции, где значение
    # добавлено, поэтому отдельным автокоммитом.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'new_fine'")


def downgrade() -> None:
    # PostgreSQL не умеет удалять значение из enum, а пересоздание типа
    # потребовало бы переписать таблицу алертов. Значение безвредно.
    pass
