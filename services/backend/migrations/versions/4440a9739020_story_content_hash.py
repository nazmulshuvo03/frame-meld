"""story content_hash

Revision ID: 4440a9739020
Revises: 8cea41eb5150
Create Date: 2026-09-13 02:08:17.499591

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4440a9739020'
down_revision: Union[str, Sequence[str], None] = '8cea41eb5150'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A rename, not a drop-and-add. Autogenerate would have emitted the
    latter and silently lost every existing hash."""
    op.alter_column("stories", "input_hash", new_column_name="content_hash")


def downgrade() -> None:
    op.alter_column("stories", "content_hash", new_column_name="input_hash")
