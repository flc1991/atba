"""Add registration_dogs table for fun run multi-dog entries

Revision ID: 3f7e1b2d4c6a
Revises: 8a4f2c1d9e3b
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '3f7e1b2d4c6a'
down_revision: Union[str, None] = '8a4f2c1d9e3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'registration_dogs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('registration_id', sa.Integer(),
                  sa.ForeignKey('registrations.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('dog_name', sa.String(255), nullable=False),
        sa.Column('dog_breed', sa.String(100), nullable=True),
        sa.Column('event_1', sa.String(100), nullable=True),
        sa.Column('event_2', sa.String(100), nullable=True),
        sa.Column('event_3', sa.String(100), nullable=True),
        sa.Column('event_4', sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('registration_dogs')
