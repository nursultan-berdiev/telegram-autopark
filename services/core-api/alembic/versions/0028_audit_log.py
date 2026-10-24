"""Единая лента аудита действий администратора.

Кто/что/когда по успешным админ-мутациям (из бота и из веб-админки). Отдельной
такой таблицы раньше не было — был только разрозненный след в доменных колонках.
Аддитивно и обратимо: downgrade просто дропает таблицу.

Revision ID: 0028_audit_log
Revises: 0027_photo_sides
Create Date: 2026-10-24

NB: id ревизии держим ≤32 символов — `alembic_version.version_num` это varchar(32).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0028_audit_log"
down_revision: Union[str, None] = "0027_photo_sides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=128), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_actor_tg_user_id", "audit_log", ["actor_tg_user_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_tg_user_id", table_name="audit_log")
    op.drop_table("audit_log")
