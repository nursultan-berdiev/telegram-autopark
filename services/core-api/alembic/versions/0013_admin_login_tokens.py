"""Одноразовые ссылки для входа в веб-админку.

Пароля у админов нет: личность подтверждает бот, который уже знает
Telegram-id. Токен хранится хешем — утечка таблицы не должна давать вход.

Revision ID: 0013_admin_login_tokens
Revises: 0012_alert_new_fine
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_admin_login_tokens"
down_revision: Union[str, None] = "0012_alert_new_fine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_login_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_admin_login_tokens_token_hash", "admin_login_tokens", ["token_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_login_tokens_token_hash", table_name="admin_login_tokens")
    op.drop_table("admin_login_tokens")
