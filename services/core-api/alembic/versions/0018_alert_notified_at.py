"""Отметка доставки алерта (один раз, независимо от перезапуска бота).

Доставку админам раньше дедуплицировал бот в памяти — после его перезапуска
все открытые алерты рассылались заново (пугающие повторы «двигатель
заблокирован»). Серверное поле `notified_at` делает доставку идемпотентной.

Revision ID: 0018_alert_notified_at
Revises: 0017_armed_block
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018_alert_notified_at"
down_revision: Union[str, None] = "0017_armed_block"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Существующие открытые алерты бот уже показывал — помечаем доставленными,
    # иначе первый опрос после выката разошлёт их заново (ровно тот повтор,
    # который этот PR и убирает). CURRENT_TIMESTAMP портируем (PostgreSQL+SQLite).
    op.execute("UPDATE alerts SET notified_at = CURRENT_TIMESTAMP WHERE status = 'open'")


def downgrade() -> None:
    op.drop_column("alerts", "notified_at")
