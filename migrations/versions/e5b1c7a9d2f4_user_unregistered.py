"""Make user.password_hash nullable and add is_unregistered

Revision ID: e5b1c7a9d2f4
Revises: d3a7c1e5b9f0
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e5b1c7a9d2f4'
down_revision: Union[str, None] = 'd3a7c1e5b9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.alter_column(
            'password_hash',
            existing_type=sa.String(255),
            nullable=True,
        )
        batch.add_column(
            sa.Column(
                'is_unregistered', sa.Boolean(),
                nullable=False, server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('is_unregistered')
        # Note: making password_hash NOT NULL again on downgrade would fail
        # if any unregistered placeholder users still exist; intentionally
        # leave it nullable on downgrade.
