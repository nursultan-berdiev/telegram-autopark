"""Один штраф на пару «машина + номер постановления».

Импорт из внешнего источника запускается по расписанию и каждый раз видит
те же постановления. Без уникального индекса повторный прогон дублировал бы
штрафы, а правило «N штрафов за окно» считало бы их как разные нарушения.

Revision ID: 0010_fine_external_ref_unique
Revises: 0009_driver_car_unique
Create Date: 2026-09-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_fine_external_ref_unique"
down_revision: Union[str, None] = "0009_driver_car_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Номер постановления бот вытаскивал из примечания эвристикой, поэтому
    # дубли в базе уже могут быть, и индекс на них не встанет. Значение не
    # выбрасываем — переносим в примечание, иначе потеряли бы данные.
    op.execute(
        sa.text(
            """
            UPDATE fines
               SET note = COALESCE(fines.note || ' ', '')
                          || '[дубль внешнего номера: ' || fines.external_ref || ']',
                   external_ref = NULL
             WHERE fines.external_ref IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM fines AS o
                    WHERE o.car_id = fines.car_id
                      AND o.external_ref = fines.external_ref
                      AND o.id < fines.id
               )
            """
        )
    )
    op.create_index(
        "uq_fine_external_ref",
        "fines",
        ["car_id", "external_ref"],
        unique=True,
        sqlite_where=sa.text("external_ref IS NOT NULL"),
        postgresql_where=sa.text("external_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_fine_external_ref", table_name="fines")
