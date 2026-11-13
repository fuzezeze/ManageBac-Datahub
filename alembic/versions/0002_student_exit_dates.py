"""Store student exit dates used to derive withdrawn and graduated status.

Revision 0001 creates tables from the current models, so a brand new database
already has these columns. This revision adds them only when upgrading a
database created before the columns existed.
"""

from alembic import op
from sqlalchemy import Column, Date, text

revision = "0002_student_exit_dates"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _existing_columns() -> set[str]:
    rows = op.get_bind().execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'hub_core'
              AND table_name = 'students'
              AND column_name IN ('withdrawn_on', 'graduated_on')
            """
        )
    )
    return {row[0] for row in rows}


def upgrade() -> None:
    existing = _existing_columns()
    if "withdrawn_on" not in existing:
        op.add_column("students", Column("withdrawn_on", Date(), nullable=True), schema="hub_core")
    if "graduated_on" not in existing:
        op.add_column("students", Column("graduated_on", Date(), nullable=True), schema="hub_core")


def downgrade() -> None:
    existing = _existing_columns()
    if "graduated_on" in existing:
        op.drop_column("students", "graduated_on", schema="hub_core")
    if "withdrawn_on" in existing:
        op.drop_column("students", "withdrawn_on", schema="hub_core")
