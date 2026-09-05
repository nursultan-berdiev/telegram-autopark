"""Чек может прийти файлом, а не только фотографией.

Добавляет payments.receipt_kind ("photo" | "document"). Нужен, чтобы переслать
чек владельцу правильным методом: file_id документа (скриншот-файл, PDF из
банковского приложения) через send_photo Telegram не принимает.

Существующие платежи — фотографии, отсюда server_default="photo".

Revision ID: 0005_receipt_kind
Revises: 0004_driver_fired
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_receipt_kind"
down_revision: Union[str, None] = "0004_driver_fired"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "receipt_kind",
            sa.String(length=16),
            nullable=False,
            server_default="photo",
        ),
    )


def downgrade() -> None:
    op.drop_column("payments", "receipt_kind")
