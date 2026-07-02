"""Make project_id nullable, add user_id to conversations

Revision ID: 3a4b5c6d7e8f
Revises: e2df87c9f208
Create Date: 2026-07-02 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "3a4b5c6d7e8f"
down_revision: Union[str, Sequence[str], None] = "e2df87c9f208"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make project_id nullable (keep FK for non-null values)
    op.alter_column("conversations", "project_id", nullable=True)

    # Add user_id column (nullable initially for backfill)
    op.add_column(
        "conversations",
        sa.Column("user_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_user_id",
        "conversations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Backfill user_id from the owning project
    op.execute("""
        UPDATE conversations c
        SET user_id = p.user_id
        FROM projects p
        WHERE c.project_id = p.id
    """)

    # Now make user_id NOT NULL
    op.alter_column("conversations", "user_id", nullable=False)

    # Composite index for efficient querying
    op.create_index(
        "ix_conversations_user_project",
        "conversations",
        ["user_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_user_project")
    op.drop_constraint("fk_conversations_user_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "user_id")
    op.alter_column("conversations", "project_id", nullable=False)
