"""Add access_token to trial_entries and registrations

Revision ID: d9f3b5c2a846
Revises: c8e2b4a91f37
Create Date: 2026-11-22

"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd9f3b5c2a846'
down_revision: Union[str, None] = 'c8e2b4a91f37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('trial_entries', 'registrations')


def upgrade() -> None:
    conn = op.get_bind()
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('access_token', sa.String(64), nullable=True))
        # Existing rows need a token too.
        t = sa.table(table, sa.column('id', sa.Integer), sa.column('access_token', sa.String))
        for (row_id,) in conn.execute(sa.select(t.c.id)).fetchall():
            conn.execute(
                t.update().where(t.c.id == row_id).values(access_token=secrets.token_urlsafe(32))
            )
        op.create_index(f'ix_{table}_access_token', table, ['access_token'], unique=True)


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f'ix_{table}_access_token', table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column('access_token')
