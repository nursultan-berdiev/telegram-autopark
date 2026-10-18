"""Фотоконтроль: ещё два ракурса — левый и правый бок машины.

Колонки nullable: осмотры, сданные до этой ревизии, остались с четырьмя фото.
Новые сдачи без боков не примет контракт API.

Revision ID: 0027_photo_sides
Revises: 0026_photo_inspections
Create Date: 2026-10-18

NB: id ревизии держим ≤32 символов — `alembic_version.version_num` это varchar(32).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0027_photo_sides"
down_revision: Union[str, None] = "0026_photo_inspections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "photo_inspections", sa.Column("left_file_id", sa.String(length=256), nullable=True)
    )
    op.add_column(
        "photo_inspections", sa.Column("right_file_id", sa.String(length=256), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("photo_inspections") as batch:
        batch.drop_column("right_file_id")
        batch.drop_column("left_file_id")
