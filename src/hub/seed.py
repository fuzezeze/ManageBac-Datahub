import logging

from sqlalchemy import text

from hub.db import session_for
from hub.scheduler import tick
from hub.web.admin_routes import ensure_bootstrap_admin
from hub.worker import run_once, worker_identity

logger = logging.getLogger(__name__)


def drain(limit: int = 40) -> None:
    identity = worker_identity()
    idle_passes = 0
    for _ in range(limit):
        if run_once(identity):
            idle_passes = 0
            continue
        session = session_for("sync")
        try:
            session.execute(text("UPDATE hub_ops.sync_jobs SET run_after = UTC_TIMESTAMP() WHERE status = 'retry_wait'"))
            session.commit()
        finally:
            session.close()
        if run_once(identity):
            idle_passes = 0
            continue
        idle_passes += 1
        if idle_passes >= 2:
            return


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ensure_bootstrap_admin()
    tick()
    drain()


if __name__ == "__main__":
    main()
