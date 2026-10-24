"""Store one LoRa receiver profile on each transmitter.

Revision ID: d4e6f8a1b3c5
Revises: c2b7e9d5f1a4
"""

import sqlalchemy as sa

from alembic import op

revision = "d4e6f8a1b3c5"
down_revision = "c2b7e9d5f1a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transmitters", sa.Column("lora_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transmitters") as batch_op:
        batch_op.drop_column("lora_config")
