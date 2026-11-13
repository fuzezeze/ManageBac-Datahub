import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from hub.catalog import DATASETS
from hub.config import get_settings
from hub.db import session_for
from hub.jobs import enqueue_job
from hub.models import ApiRequestLog, ApiResponse, ApiUsageBucket, SyncSchedule
from hub.timeutil import utcnow

logger = logging.getLogger(__name__)


def ensure_schedules(session) -> None:
    for spec in DATASETS.values():
        if not spec.scheduled:
            continue
        if session.get(SyncSchedule, spec.name) is None:
            session.add(
                SyncSchedule(
                    dataset=spec.name,
                    cadence=spec.cadence,
                    hour=spec.hour,
                    minute=spec.minute,
                    interval_minutes=spec.interval_minutes,
                    enabled=True,
                )
            )


def current_slot(schedule: SyncSchedule, now_local: datetime) -> datetime:
    if schedule.cadence == "daily":
        slot = now_local.replace(hour=schedule.hour or 0, minute=schedule.minute, second=0, microsecond=0)
        if now_local < slot:
            slot -= timedelta(days=1)
        return slot
    if schedule.cadence == "hourly":
        slot = now_local.replace(minute=schedule.minute, second=0, microsecond=0)
        if now_local < slot:
            slot -= timedelta(hours=1)
        return slot
    minutes = schedule.interval_minutes or 15
    epoch = int(now_local.timestamp() // 60)
    bucket = epoch - (epoch % minutes)
    return datetime.fromtimestamp(bucket * 60, tz=now_local.tzinfo)


def tick(now: datetime | None = None) -> int:
    settings = get_settings()
    moment = now or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(settings.zone())
    session = session_for("sync")
    created = 0
    try:
        ensure_schedules(session)
        schedules = session.scalars(select(SyncSchedule).where(SyncSchedule.enabled.is_(True))).all()
        for schedule in schedules:
            spec = DATASETS.get(schedule.dataset)
            if spec is None or spec.blocked:
                continue
            slot = current_slot(schedule, local)
            if local < slot:
                continue
            scope: dict = {"mode": "full"}
            if spec.supports_dates:
                end = local.date()
                start = end - timedelta(days=settings.attendance_lookback_days)
                scope = {"mode": "full", "date_from": start.isoformat(), "date_to": end.isoformat()}
            key = f"schedule:{schedule.dataset}:{slot.astimezone(timezone.utc).isoformat()}"
            existing_id = enqueue_job(
                session,
                dataset=schedule.dataset,
                trigger="schedule",
                idempotency_key=key,
                scope=scope,
            )
            if existing_id.created_at >= moment - timedelta(seconds=2) and existing_id.trigger == "schedule":
                created += 1
        session.commit()
        _purge(session)
        session.commit()
    finally:
        session.close()
    return created


def _purge(session) -> None:
    settings = get_settings()
    now = utcnow()
    if settings.raw_retention_days:
        cutoff = now - timedelta(days=settings.raw_retention_days)
        session.execute(delete(ApiResponse).where(ApiResponse.fetched_at < cutoff))
    if settings.audit_retention_days:
        cutoff = now - timedelta(days=settings.audit_retention_days)
        admin = session_for("admin")
        try:
            admin.execute(delete(ApiRequestLog).where(ApiRequestLog.requested_at < cutoff))
            admin.execute(delete(ApiUsageBucket).where(ApiUsageBucket.bucket_start < cutoff))
            admin.commit()
        finally:
            admin.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("scheduler starting")
    while True:
        tick()
        time.sleep(30)
