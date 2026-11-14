"""Индексы под главную админки и страницу машины.

Оплаты и осмотры теперь читаются по машине (последняя оплата, история оплат,
осмотры машины), а индексы были только по водителю. Аддитивно и обратимо.

Revision ID: 0029_overview_indexes
Revises: 0028_audit_log
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0029_overview_indexes"
down_revision: Union[str, None] = "0028_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_payments_car_id", "payments", ["car_id"])
    op.create_index(
        "ix_photo_inspections_car_created", "photo_inspections", ["car_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_photo_inspections_car_created", table_name="photo_inspections")
    op.drop_index("ix_payments_car_id", table_name="payments")
