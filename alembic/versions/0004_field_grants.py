"""Per-client field grants for read APIs.

Revision 0001 creates tables from the current models, so a brand new database
already has client_field_grants. This revision updates databases created before
that table existed and grants the API users access to it.
"""

from alembic import op
from sqlalchemy import text

from hub.models import ClientFieldGrant

revision = "0004_field_grants"
down_revision = "0003_notes"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    rows = op.get_bind().execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'hub_ops'
              AND table_name = 'client_field_grants'
            """
        )
    )
    return {row[0] for row in rows}


def upgrade() -> None:
    bind = op.get_bind()
    if "client_field_grants" not in _tables():
        ClientFieldGrant.__table__.create(bind)
    bind.execute(text("GRANT SELECT ON hub_ops.client_field_grants TO 'hub_api'@'%', 'hub_auth'@'%'"))
    bind.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON hub_ops.client_field_grants TO 'hub_admin'@'%'"))


def downgrade() -> None:
    bind = op.get_bind()
    if "client_field_grants" in _tables():
        bind.execute(text("DROP TABLE hub_ops.client_field_grants"))
