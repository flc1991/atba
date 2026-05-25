"""Add ahba_event_1_judge / ahba_event_2_judge to trials

Revision ID: a4d6f8e2c3b9
Revises: 9c5a8b1d2e7f
Create Date: 2026-05-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a4d6f8e2c3b9'
down_revision: Union[str, None] = '9c5a8b1d2e7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('trials') as batch:
        batch.add_column(sa.Column('ahba_event_1_judge', sa.String(255), nullable=True))
        batch.add_column(sa.Column('ahba_event_2_judge', sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('trials') as batch:
        batch.drop_column('ahba_event_2_judge')
        batch.drop_column('ahba_event_1_judge')
