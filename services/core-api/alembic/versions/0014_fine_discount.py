"""Сумма штрафа со скидкой и срок скидки.

tolom.kg отдаёт две суммы: полную и уменьшенную при оплате в срок
(`fineAmountToPay` + `discountDaysLeft`). Одной колонкой их не покрыть —
после истечения скидки любая из цифр по отдельности врёт, а пересчитать
её будет нечем: скидку назначает сервис, а не мы.

Revision ID: 0014_fine_discount
Revises: 0013_admin_login_tokens
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_fine_discount"
down_revision: Union[str, None] = "0013_admin_login_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Нулевого значения нет намеренно: у штрафов из carcheck и заведённых
    # руками скидка неизвестна, а 0 читался бы как «платить нечего».
    op.add_column("fines", sa.Column("amount_to_pay", sa.Numeric(12, 2), nullable=True))
    op.add_column("fines", sa.Column("discount_days_left", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("fines", "discount_days_left")
    op.drop_column("fines", "amount_to_pay")
