"""users.token_version（单点登录会话版本号）

Revision ID: 7c31f0a9b2e4
Revises: 49585dd54795
Create Date: 2026-08-12

登录时 token_version 自增并写入 JWT；校验时与库中值比对，实现同账号互踢。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "7c31f0a9b2e4"
down_revision: Union[str, None] = "49585dd54795"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("users", "token_version")
