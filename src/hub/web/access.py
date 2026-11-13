import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from hub.config import get_settings
from hub.models import Alert, ApiClient, ApiCredential, ApiRequestLog, ApiUsageBucket, ClientPermission, ClientRange, QuotaCounter
from hub.secrets import hash_secret
from hub.timeutil import utcnow


class ApiFailure(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class QuotaExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("quota exceeded")
        self.retry_after = retry_after


class AuditPersistenceError(Exception):
    pass


@dataclass
class Principal:
    client_id: UUID
    credential_id: UUID
    name: str
    scopes: set[str]
    requests_per_minute: int
    records_per_day: int
    bytes_per_day: int
    ranges: dict[str, set[str]] = field(default_factory=dict)


def new_request_id() -> str:
    return "req_" + uuid4().hex


def dumps(payload: dict) -> bytes:
    return json.dumps(payload, default=_json_default, separators=(",", ":")).encode()


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"cannot encode {type(value)}")


def error_payload(request_id: str, code: str, message: str) -> bytes:
    return dumps({"error": {"request_id": request_id, "code": code, "message": message}})


def authenticate(session: Session, token: str | None) -> Principal | None:
    if not token:
        return None
    digest = hash_secret(token)
    credential = session.scalars(select(ApiCredential).where(ApiCredential.secret_hash == digest)).one_or_none()
    if credential is None or credential.revoked_at is not None or credential.expires_at <= utcnow():
        return None
    client = session.get(ApiClient, credential.client_id)
    if client is None or not client.enabled:
        return None
    scopes = set(session.scalars(select(ClientPermission.scope).where(ClientPermission.client_id == client.id)).all())
    ranges: dict[str, set[str]] = {}
    for row in session.scalars(select(ClientRange).where(ClientRange.client_id == client.id)).all():
        ranges.setdefault(row.dimension, set()).add(row.mb_id)
    return Principal(
        client_id=client.id,
        credential_id=credential.id,
        name=client.name,
        scopes=scopes,
        requests_per_minute=client.requests_per_minute,
        records_per_day=client.records_per_day,
        bytes_per_day=client.bytes_per_day,
        ranges=ranges,
    )


def clean_limit(value: int | None) -> int:
    settings = get_settings()
    if value is None:
        return settings.page_size_default
    if value < 1 or value > settings.page_size_max:
        raise ApiFailure(400, "PAGE_SIZE_INVALID", f"page size must be between 1 and {settings.page_size_max}")
    return value


def filter_hash(filters: dict) -> str:
    raw = json.dumps(filters, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def encode_cursor(client_id: UUID, digest: str, keys: list[str]) -> str:
    payload = json.dumps({"v": 1, "client_id": str(client_id), "filter_hash": digest, "keys": keys}, separators=(",", ":"))
    signature = hmac.new(get_settings().cursor_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{signature}".encode()).decode()


def decode_cursor(token: str, client_id: UUID, digest: str) -> list[str]:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        payload, signature = raw.rsplit(".", 1)
        expected = hmac.new(get_settings().cursor_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ApiFailure(400, "CURSOR_INVALID", "cursor does not match this query")
        data = json.loads(payload)
    except ApiFailure:
        raise
    except Exception as exc:
        raise ApiFailure(400, "CURSOR_INVALID", "cursor is not valid") from exc
    if data.get("client_id") != str(client_id) or data.get("filter_hash") != digest:
        raise ApiFailure(400, "CURSOR_INVALID", "cursor does not match this caller or filter")
    return [str(item) for item in data["keys"]]


def auth_failure_limited(session: Session, source_ip: str | None) -> bool:
    if not source_ip:
        return False
    since = utcnow() - timedelta(minutes=10)
    count = session.scalar(
        select(func.count())
        .select_from(ApiRequestLog)
        .where(
            ApiRequestLog.authenticated.is_(False),
            ApiRequestLog.source_ip == source_ip,
            ApiRequestLog.requested_at >= since,
        )
    )
    return int(count or 0) >= 30


def log_anonymous(session: Session, **kwargs) -> None:
    try:
        _insert_log(session, authenticated=False, client_id=None, credential_id=None, **kwargs)
        session.commit()
    except Exception:
        session.rollback()


def persist_access(
    session: Session,
    *,
    principal: Principal,
    request_id: str,
    method: str,
    route: str,
    filters: dict,
    status: int,
    error_code: str | None,
    returned_count: int,
    body: bytes,
    source_ip: str | None,
    user_agent: str | None,
    started: datetime,
) -> None:
    """Commit the audit row and quota before the caller returns the body."""
    try:
        limited = _apply_quota_and_audit(
            session,
            principal=principal,
            request_id=request_id,
            method=method,
            route=route,
            filters=filters,
            status=status,
            error_code=error_code,
            returned_count=returned_count,
            body=body,
            source_ip=source_ip,
            user_agent=user_agent,
            started=started,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise AuditPersistenceError() from exc
    try:
        if limited is None:
            _maybe_spike(session, principal)
            session.commit()
    except Exception:
        session.rollback()
    if limited is not None:
        raise QuotaExceeded(60)


def _apply_quota_and_audit(session: Session, *, principal: Principal, request_id: str, method: str, route: str, filters: dict, status: int, error_code: str | None, returned_count: int, body: bytes, source_ip: str | None, user_agent: str | None, started: datetime) -> None:
    now = utcnow()
    minute = _lock_counter(session, principal.client_id, "minute", now.replace(second=0, microsecond=0))
    day = _lock_counter(session, principal.client_id, "day", now.replace(hour=0, minute=0, second=0, microsecond=0))
    size = len(body)
    limited = None
    if minute.requests + 1 > principal.requests_per_minute:
        limited = "rate"
    elif day.records + returned_count > principal.records_per_day or day.bytes + size > principal.bytes_per_day:
        limited = "volume"
    minute.requests += 1
    if limited is None:
        day.records += returned_count
        day.bytes += size
        http_status = status
        count = returned_count
        code = error_code
        response_bytes = size
    else:
        http_status = 429
        count = 0
        code = "QUOTA_EXCEEDED"
        response_bytes = 0
        _open_alert(
            session,
            principal,
            "quota",
            "API quota exceeded",
            {"limit": limited, "route": route, "request_id": request_id},
        )
    _insert_log(
        session,
        authenticated=True,
        client_id=principal.client_id,
        credential_id=principal.credential_id,
        request_id=request_id,
        method=method,
        route=route,
        filters=filters,
        status=http_status,
        error_code=code,
        returned_count=count,
        response_bytes=response_bytes,
        source_ip=source_ip,
        user_agent=user_agent,
        started=started,
    )
    _bump_usage(session, principal.client_id, route, now, success=http_status < 400, records=count, nbytes=response_bytes)
    return limited


def _lock_counter(session: Session, client_id: UUID, kind: str, start: datetime) -> QuotaCounter:
    session.execute(
        text(
            """
            INSERT INTO hub_ops.quota_counters (id, client_id, window_kind, window_start, requests, records, bytes)
            VALUES (:id, :client_id, :kind, :start, 0, 0, 0)
            ON DUPLICATE KEY UPDATE id = id
            """
        ),
        {
            "id": uuid4().hex,
            "client_id": client_id.hex,
            "kind": kind,
            "start": start.astimezone(timezone.utc).replace(tzinfo=None),
        },
    )
    return session.scalars(
        select(QuotaCounter)
        .where(QuotaCounter.client_id == client_id, QuotaCounter.window_kind == kind, QuotaCounter.window_start == start)
        .with_for_update()
    ).one()


def _insert_log(session: Session, *, authenticated: bool, client_id, credential_id, request_id: str, method: str, route: str, filters: dict, status: int, error_code: str | None, returned_count: int, response_bytes: int, source_ip: str | None, user_agent: str | None, started: datetime) -> None:
    completed = utcnow()
    session.add(
        ApiRequestLog(
            request_id=request_id,
            client_id=client_id,
            credential_id=credential_id,
            requested_at=started,
            completed_at=completed,
            duration_ms=max(int((completed - started).total_seconds() * 1000), 0),
            http_method=method,
            route_template=route,
            filter_summary={key: value for key, value in filters.items() if value is not None},
            http_status=status,
            error_code=error_code,
            returned_record_count=returned_count,
            response_bytes=response_bytes,
            source_ip=source_ip,
            user_agent=(user_agent or "")[:200] or None,
            authenticated=authenticated,
        )
    )


def _bump_usage(session: Session, client_id: UUID, route: str, now: datetime, *, success: bool, records: int, nbytes: int) -> None:
    for size, start in (
        ("minute", now.replace(second=0, microsecond=0)),
        ("hour", now.replace(minute=0, second=0, microsecond=0)),
        ("day", now.replace(hour=0, minute=0, second=0, microsecond=0)),
    ):
        bucket = session.scalars(
            select(ApiUsageBucket).where(
                ApiUsageBucket.client_id == client_id,
                ApiUsageBucket.bucket_size == size,
                ApiUsageBucket.bucket_start == start,
                ApiUsageBucket.route_template == route,
            )
        ).one_or_none()
        if bucket is None:
            bucket = ApiUsageBucket(
                client_id=client_id,
                bucket_size=size,
                bucket_start=start,
                route_template=route,
                request_count=0,
                success_count=0,
                error_count=0,
                record_count=0,
                byte_count=0,
            )
            session.add(bucket)
        bucket.request_count += 1
        bucket.success_count += 1 if success else 0
        bucket.error_count += 0 if success else 1
        bucket.record_count += records
        bucket.byte_count += nbytes


def _open_alert(session: Session, principal: Principal, kind: str, message: str, details: dict) -> None:
    recent = session.scalars(
        select(Alert)
        .where(
            Alert.client_id == principal.client_id,
            Alert.kind == kind,
            Alert.created_at >= utcnow() - timedelta(minutes=15),
        )
        .limit(1)
    ).first()
    if recent is not None:
        return
    session.add(
        Alert(
            school_id=get_settings().school_id,
            kind=kind,
            client_id=principal.client_id,
            message=message,
            details=details,
            status="open",
            created_at=utcnow(),
        )
    )


def _maybe_spike(session: Session, principal: Principal) -> None:
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_records = session.scalar(
        select(func.coalesce(func.sum(ApiUsageBucket.record_count), 0)).where(
            ApiUsageBucket.client_id == principal.client_id,
            ApiUsageBucket.bucket_size == "day",
            ApiUsageBucket.bucket_start == today,
        )
    )
    previous = session.scalar(
        select(func.avg(ApiUsageBucket.record_count)).where(
            ApiUsageBucket.client_id == principal.client_id,
            ApiUsageBucket.bucket_size == "day",
            ApiUsageBucket.bucket_start < today,
            ApiUsageBucket.bucket_start >= today - timedelta(days=7),
        )
    )
    if previous and float(previous) >= 20 and float(today_records or 0) > float(previous) * 5:
        _open_alert(
            session,
            principal,
            "usage_spike",
            "Record volume is far above this client's recent baseline",
            {"today_records": int(today_records or 0), "baseline": float(previous)},
        )
