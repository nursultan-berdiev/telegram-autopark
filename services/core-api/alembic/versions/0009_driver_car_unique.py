"""Одна машина — один действующий водитель, на уровне БД.

Проверка «машина свободна» при регистрации по ссылке — это check-then-act:
две параллельные регистрации по одному инвайту обе видели свободную машину
и обе назначали водителя. Частичный уникальный индекс закрывает гонку.

Revision ID: 0009_driver_car_unique
Revises: 0008_rules_alerts_commands
Create Date: 2026-09-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_driver_car_unique"
down_revision: Union[str, None] = "0008_rules_alerts_commands"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_driver_active_car",
        "drivers",
        ["car_id"],
        unique=True,
        postgresql_where=sa.text("active AND car_id IS NOT NULL"),
        sqlite_where=sa.text("active = 1 AND car_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_driver_active_car", table_name="drivers")
