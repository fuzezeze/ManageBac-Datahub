"""Report grants and the analytics views the report catalog reads.

Revision 0001 creates tables from the current models and installs REPORT_VIEWS,
so a brand new database already has both. This revision updates databases created
before report grants existed.
"""

from alembic import op
from sqlalchemy import text

from hub.analytics_views import REPORT_VIEW_NAMES, REPORT_VIEWS
from hub.models import ReportGrant

revision = "0005_report_grants"
down_revision = "0004_field_grants"
branch_labels = None
depends_on = None


def _has_table() -> bool:
    rows = op.get_bind().execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'hub_ops'
              AND table_name = 'report_grants'
            """
        )
    )
    return any(rows)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table():
        ReportGrant.__table__.create(bind)
    for statement in REPORT_VIEWS:
        bind.execute(text(statement))
    bind.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON hub_ops.report_grants TO 'hub_admin'@'%'"))


def downgrade() -> None:
    bind = op.get_bind()
    for name in REPORT_VIEW_NAMES:
        bind.execute(text(f"DROP VIEW IF EXISTS hub_analytics.{name}"))
    if _has_table():
        bind.execute(text("DROP TABLE hub_ops.report_grants"))
