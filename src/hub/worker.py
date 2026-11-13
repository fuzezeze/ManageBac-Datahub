import logging
import os
import socket
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from hub.catalog import DATASETS
from hub.config import get_settings
from hub.connector import AuthError, ContractError, ManageBacClient, ManageBacError, RateLimited, TransientError
from hub.db import session_for
from hub.jobs import safe_summary
from hub.models import SyncAttempt, SyncJob
from hub.normalize import ShapeError
from hub.pipeline import LeaseLost, _DependencyWait, run_dataset
from hub.publish import write_state
from hub.timeutil import utcnow

logger = logging.getLogger(__name__)


def worker_identity() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"


def recover_expired_leases(session: Session) -> None:
    settings = get_settings()
    now = utcnow()
    expired = session.scalars(
        select(SyncJob).where(SyncJob.status == "running", SyncJob.lease_expires_at.is_not(None), SyncJob.lease_expires_at < now)
    ).all()
    for job in expired:
        if job.attempt_count >= settings.max_transient_attempts:
            job.status = "failed"
            job.error_code = "lease_expired"
            job.error_summary = "The worker lease expired and the attempt budget was exhausted."
            job.finished_at = now
            job.locked_by = None
        else:
            job.status = "queued"
            job.locked_by = None
            job.lease_expires_at = None


def claim_job(session: Session, worker_id: str) -> SyncJob | None:
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import aliased

    settings = get_settings()
    now = utcnow()
    recover_expired_leases(session)
    other = aliased(SyncJob)
    candidate = session.scalars(
        select(SyncJob)
        .where(
            SyncJob.status.in_(("queued", "retry_wait")),
            or_(SyncJob.run_after.is_(None), SyncJob.run_after <= now),
            ~select(other.id)
            .where(
                other.school_id == SyncJob.school_id,
                other.dataset == SyncJob.dataset,
                other.status == "running",
                other.lease_expires_at > now,
            )
            .exists(),
        )
        .order_by(SyncJob.priority, SyncJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if candidate is None:
        session.commit()
        return None
    candidate.status = "running"
    candidate.locked_by = worker_id
    candidate.lease_expires_at = now + timedelta(seconds=settings.worker_lease_seconds)
    candidate.heartbeat_at = now
    candidate.started_at = candidate.started_at or now
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    return candidate


def run_once(worker_id: str, client: ManageBacClient | None = None) -> bool:
    settings = get_settings()
    if client is None and not settings.fixture_allowed() and settings.mb_auth_mode == "fixture":
        raise RuntimeError("fixture mode is disabled in production")
    session = session_for("sync")
    try:
        job = claim_job(session, worker_id)
    finally:
        session.close()
    if job is None:
        return False
    owned = session_for("sync")
    try:
        _execute_claimed(owned, job.id, worker_id, client)
    finally:
        owned.close()
    return True


def _execute_claimed(session: Session, job_id, worker_id: str, client: ManageBacClient | None) -> None:
    job = session.get(SyncJob, job_id)
    if job is None or job.locked_by != worker_id:
        return
    spec = DATASETS[job.dataset]
    if spec.blocked:
        write_state(
            session,
            school_id=job.school_id,
            dataset=job.dataset,
            attempt_status="blocked",
            record_count=0,
            error_summary=spec.block_reason,
            succeeded=False,
        )
        _finish_job(session, job, status="failed", code="blocked", summary=spec.block_reason, counts={})
        session.commit()
        return
    settings = get_settings()
    mb = client or ManageBacClient(settings)
    attempt_number = job.attempt_count + 1
    attempt = SyncAttempt(
        job_id=job.id,
        attempt_number=attempt_number,
        started_at=utcnow(),
        status="running",
        counts={},
    )
    job.attempt_count = attempt_number
    session.add(attempt)
    session.commit()
    try:
        counts = run_dataset(session, mb, job, worker_id)
    except LeaseLost:
        session.rollback()
        logger.warning("worker %s lost the lease for job %s", worker_id, job_id)
        return
    except _DependencyWait as exc:
        session.rollback()
        job = session.get(SyncJob, job_id)
        if job and job.locked_by == worker_id:
            job.status = "retry_wait"
            job.locked_by = None
            job.lease_expires_at = None
            job.run_after = utcnow() + timedelta(seconds=5)
            job.error_code = "dependency_wait"
            job.error_summary = f"Waiting for {exc.dataset} to succeed."
            job.attempt_count = max(job.attempt_count - 1, 0)
            attempt = session.scalars(select(SyncAttempt).where(SyncAttempt.job_id == job.id).order_by(SyncAttempt.started_at.desc())).first()
            if attempt and attempt.finished_at is None:
                attempt.finished_at = utcnow()
                attempt.status = "retry_wait"
                attempt.error_code = "dependency_wait"
            session.commit()
        return
    except (AuthError, ContractError, ShapeError, RateLimited, TransientError, ManageBacError) as exc:
        session.rollback()
        job = session.get(SyncJob, job_id)
        if job is None or job.locked_by != worker_id:
            return
        published = _published_partial(job)
        status, run_after = _failure_status(session, job, exc, published)
        _finish_job(session, job, status=status, code=getattr(exc, "code", "error"), summary=safe_summary(exc), counts=job.counts or {}, run_after=run_after)
        _close_attempt(session, job, status, getattr(exc, "code", "error"), safe_summary(exc))
        if status == "failed":
            write_state(
                session,
                school_id=job.school_id,
                dataset=job.dataset,
                attempt_status="failed",
                record_count=None,
                error_summary=safe_summary(exc),
                succeeded=False,
            )
        session.commit()
        return
    else:
        job = session.get(SyncJob, job_id)
        if job is None or job.locked_by != worker_id:
            session.rollback()
            return
        _finish_job(session, job, status="succeeded", code=None, summary=None, counts=counts.as_dict())
        _close_attempt(session, job, "succeeded", None, None, counts.as_dict())
        session.commit()
    finally:
        if client is None:
            mb.close()


def _published_partial(job: SyncJob) -> bool:
    counts = job.counts or {}
    return any(counts.get(key, 0) for key in ("created", "updated", "unchanged", "fetched"))


def _failure_status(session: Session, job: SyncJob, exc: Exception, published: bool) -> tuple[str, object]:
    settings = get_settings()
    now = utcnow()
    if isinstance(exc, (AuthError, ContractError, ShapeError)):
        return ("partial_failed" if published else "failed"), None
    if isinstance(exc, RateLimited):
        previous = _attempt_code_count(session, job.id, "rate_limited")
        if previous >= settings.max_rate_limit_attempts:
            return ("partial_failed" if published else "failed"), None
        return "retry_wait", now + timedelta(seconds=max(exc.retry_after_seconds, 1))
    previous = _attempt_code_count(session, job.id, "transient")
    if previous >= settings.max_transient_attempts:
        return ("partial_failed" if published else "failed"), None
    delay = min(2 ** max(previous, 0), 900)
    return "retry_wait", now + timedelta(seconds=delay)


def _attempt_code_count(session: Session, job_id, code: str) -> int:
    rows = session.scalars(select(SyncAttempt).where(SyncAttempt.job_id == job_id, SyncAttempt.error_code == code)).all()
    return len(rows) + 1


def _finish_job(session, job: SyncJob, *, status: str, code: str | None, summary: str | None, counts: dict, run_after=None) -> None:
    now = utcnow()
    job.status = status
    job.error_code = code
    job.error_summary = summary
    job.counts = counts or job.counts or {}
    job.finished_at = now if status not in {"retry_wait", "queued", "running"} else job.finished_at
    if status in {"succeeded", "failed", "partial_failed"}:
        job.locked_by = None
        job.lease_expires_at = None
    if status == "retry_wait":
        job.locked_by = None
        job.lease_expires_at = None
        job.run_after = run_after


def _close_attempt(session, job: SyncJob, status: str, code: str | None, summary: str | None, counts: dict | None = None) -> None:
    attempt = session.scalars(
        select(SyncAttempt).where(SyncAttempt.job_id == job.id).order_by(SyncAttempt.started_at.desc())
    ).first()
    if attempt is None:
        return
    attempt.finished_at = utcnow()
    attempt.status = status
    attempt.error_code = code
    attempt.error_summary = summary
    if counts is not None:
        attempt.counts = counts


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if not settings.fixture_allowed():
        raise SystemExit("fixture mode is disabled in production")
    identity = worker_identity()
    logger.info("sync worker %s starting", identity)
    import time

    while True:
        worked = run_once(identity)
        if not worked:
            time.sleep(2)
