"""Авто-блокировка двигателя за неоплату: источник команды и типы алертов.

Колонка `commands.source` (manual/overdue) — явный инициатор команды: по ней
авто-разблокировка снимает только свой блок и не трогает ручной (NULL-эвристика
по requested_by была бы хрупкой). Плюс аддитивные типы системных алертов:
`overdue_block_fired` (авто-блокировка сработала), `overdue_unblock` (оплата
закрыла долг — разблокировано), `overdue_unblock_failed` (разблокировку не
удалось отправить). ADD VALUE — на SQLite enum строкой, там no-op.

Revision ID: 0019_overdue_enforcement_alerts
Revises: 0018_alert_notified_at
Create Date: 2026-10-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_overdue_enforcement_alerts"
down_revision: Union[str, None] = "0018_alert_notified_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "commands",
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="manual"
        ),
    )
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ADD VALUE нельзя в той же транзакции, где значение используется —
    # отдельным автокоммитом (как в 0012/0017).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'overdue_block_fired'")
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'overdue_unblock'")
        op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'overdue_unblock_failed'")


def downgrade() -> None:
    op.drop_column("commands", "source")
    # PostgreSQL не умеет удалять значение из enum; значения безвредны.
