"""Add event_N_judged columns to registration_dogs

Revision ID: 9c5a8b1d2e7f
Revises: 7b2a4e5c9d1f
Create Date: 2026-05-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '9c5a8b1d2e7f'
down_revision: Union[str, None] = '7b2a4e5c9d1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('registration_dogs') as batch:
        batch.add_column(sa.Column('event_1_judged', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('event_2_judged', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('event_3_judged', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('event_4_judged', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('registration_dogs') as batch:
        batch.drop_column('event_4_judged')
        batch.drop_column('event_3_judged')
        batch.drop_column('event_2_judged')
        batch.drop_column('event_1_judged')
