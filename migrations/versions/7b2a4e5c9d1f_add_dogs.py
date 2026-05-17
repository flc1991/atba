"""Add dogs table for saved dog profiles

Revision ID: 7b2a4e5c9d1f
Revises: 3f7e1b2d4c6a
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '7b2a4e5c9d1f'
down_revision: Union[str, None] = '3f7e1b2d4c6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dogs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('dog_name', sa.String(255), nullable=False),
        sa.Column('dog_breed', sa.String(100), nullable=True),
        sa.Column('dog_sex', sa.String(1), nullable=True),
        sa.Column('dog_sire', sa.String(255), nullable=True),
        sa.Column('dog_dam', sa.String(255), nullable=True),
        sa.Column('dog_breeder', sa.String(255), nullable=True),
        sa.Column('dog_call_name', sa.String(255), nullable=True),
        sa.Column('akc_number_type', sa.String(20), nullable=True),
        sa.Column('akc_registration_number', sa.String(100), nullable=True),
        sa.Column('akc_foreign_country', sa.String(100), nullable=True),
        sa.Column('dog_dob', sa.Date(), nullable=True),
        sa.Column('ahba_registration_number', sa.String(100), nullable=True),
        sa.Column('dog_place_of_birth', sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('dogs')
