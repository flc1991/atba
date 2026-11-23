"""Add ahba_number_type to dogs and trial_entries

Revision ID: c8e2b4a91f37
Revises: f7c4a8e2d5b3
Create Date: 2026-06-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c8e2b4a91f37'
down_revision: Union[str, None] = 'f7c4a8e2d5b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('dogs') as batch:
        batch.add_column(sa.Column('ahba_number_type', sa.String(50), nullable=True))
    with op.batch_alter_table('trial_entries') as batch:
        batch.add_column(sa.Column('ahba_number_type', sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('trial_entries') as batch:
        batch.drop_column('ahba_number_type')
    with op.batch_alter_table('dogs') as batch:
        batch.drop_column('ahba_number_type')
