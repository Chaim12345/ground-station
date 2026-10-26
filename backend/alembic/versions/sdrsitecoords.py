"""Store observer coordinates on each SDR (remote station sites).

Revision ID: e5f7a9b1c3d6
Revises: d4e6f8a1b3c5
"""

import sqlalchemy as sa

from alembic import op

revision = "e5f7a9b1c3d6"
down_revision = "d4e6f8a1b3c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sdrs") as batch_op:
        batch_op.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("longitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("altitude", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sdrs") as batch_op:
        batch_op.drop_column("altitude")
        batch_op.drop_column("longitude")
        batch_op.drop_column("latitude")
