import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.catalog import DATASETS
from hub.config import get_settings
from hub.models import SyncJob
from hub.timeutil import utcnow


class DatasetBlocked(Exception):
    def __init__(self, dataset: str, reason: str):
        super().__init__(reason)
        self.dataset = dataset
        self.reason = reason


_SECRET_RE = re.compile(r"(hub_[A-Za-z0-9_\-]+|Bearer\s+\S+|auth-token:\s*\S+)", re.IGNORECASE)


def safe_summary(exc: BaseException) -> str:
    text = _SECRET_RE.sub("[redacted]", str(exc))
    return text[:500]


def find_active_job(session: Session, dataset: str, scope: dict) -> SyncJob | None:
    rows = session.scalars(
        select(SyncJob).where(
            SyncJob.school_id == get_settings().school_id,
            SyncJob.dataset == dataset,
            SyncJob.status.in_(("queued", "running", "retry_wait")),
        )
    )
    for row in rows:
        if not scope.get("date_from") and not row.scope.get("date_from"):
            return row
        if row.scope.get("date_from") == scope.get("date_from") and row.scope.get("date_to") == scope.get("date_to"):
            return row
    return None


def enqueue_job(
    session: Session,
    *,
    dataset: str,
    trigger: str,
    idempotency_key: str,
    scope: dict | None = None,
    requested_by: str | None = None,
    run_after: datetime | None = None,
) -> SyncJob:
    spec = DATASETS[dataset]
    if spec.blocked:
        raise DatasetBlocked(dataset, spec.block_reason)
    settings = get_settings()
    payload = dict(scope or {"mode": "full"})
    existing = session.scalars(
        select(SyncJob).where(SyncJob.school_id == settings.school_id, SyncJob.idempotency_key == idempotency_key)
    ).one_or_none()
    if existing is not None:
        return existing
    active = find_active_job(session, dataset, payload)
    if active is not None:
        return active
    job = SyncJob(
        school_id=settings.school_id,
        dataset=dataset,
        priority=spec.priority,
        trigger=trigger,
        requested_by=requested_by,
        scope=payload,
        idempotency_key=idempotency_key,
        status="queued",
        run_after=run_after,
        attempt_count=0,
        counts={},
        created_at=utcnow(),
    )
    session.add(job)
    session.flush()
    return job


def retry_delay(attempt: int, *, minimum: float = 0) -> timedelta:
    base = min(2 ** max(attempt - 1, 0), 900)
    return timedelta(seconds=max(base, minimum))
