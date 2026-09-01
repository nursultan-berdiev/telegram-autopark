"""Увольнение водителя: архивирование, а не удаление.

Добавляет drivers.active и drivers.fired_at. Уволенный водитель теряет доступ
к боту, машина освобождается, график останавливается — но запись и вся история
платежей сохраняются (payments.driver_id стоит на CASCADE, поэтому удалять
водителя нельзя: пропала бы выписка).

Revision ID: 0004_driver_fired
Revises: 0003_schedule_remind
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_driver_fired"
down_revision: Union[str, None] = "0003_schedule_remind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "drivers",
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.add_column(
        "drivers",
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drivers", "fired_at")
    op.drop_column("drivers", "active")
