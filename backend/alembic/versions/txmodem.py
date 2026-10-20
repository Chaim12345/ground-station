"""Store decoder modem settings on transmitter records.

Revision ID: c2b7e9d5f1a4
Revises: e91b8c4d7a20
"""

import sqlalchemy as sa

from alembic import op

revision = "c2b7e9d5f1a4"
down_revision = "e91b8c4d7a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transmitters") as batch_op:
        batch_op.add_column(sa.Column("deviation", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("framing", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transmitters") as batch_op:
        batch_op.drop_column("framing")
        batch_op.drop_column("deviation")
