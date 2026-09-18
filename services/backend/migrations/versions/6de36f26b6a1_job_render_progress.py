"""job render progress

Revision ID: 6de36f26b6a1
Revises: 782fe8813ebd
Create Date: 2026-09-19 01:45:04.836686

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6de36f26b6a1'
down_revision: Union[str, Sequence[str], None] = '782fe8813ebd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add jobs.progress_pct.

    Nullable with no server default, and correct as autogenerate wrote it
    for once: NULL is the value this column *wants* for every existing row,
    because only the two render kinds ever report progress and "this job has
    no notion of progress" is a different fact from "this render is at 0%".
    A NOT NULL DEFAULT 0 would have needed the two-step dance M3's moderation
    migration did, and would have made every script job look like a render
    that never started.
    """
    op.add_column('jobs', sa.Column('progress_pct', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Drop it. Nothing else reads the column."""
    op.drop_column('jobs', 'progress_pct')
