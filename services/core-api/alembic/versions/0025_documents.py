"""Документы машин и общие документы парка (файлы — по Telegram file_id).

Revision ID: 0025_documents
Revises: 0023_digest_time_2210
Create Date: 2026-10-17

NB: id ревизии держим ≤32 символов — `alembic_version.version_num` это varchar(32).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_documents"
down_revision: Union[str, None] = "0023_digest_time_2210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("file_id", sa.String(length=256), nullable=False),
        sa.Column("file_kind", sa.String(length=16), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_documents_car_id", "documents", ["car_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_car_id", table_name="documents")
    op.drop_table("documents")
