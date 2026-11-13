from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select

from hub.catalog import DATASETS, SCOPES
from hub.config import get_settings
from hub.db import session_for
from hub.jobs import DatasetBlocked, enqueue_job
from hub.models import (
    AdminAuditLog,
    AdminSession,
    AdminUser,
    Alert,
    ApiClient,
    ApiCredential,
    ApiRequestLog,
    ClientPermission,
    ClientRange,
    ConnectionCheck,
    DatasetSyncState,
    QualityIssue,
    SyncAttempt,
    SyncJob,
)
from hub.secrets import hash_password, hash_secret, new_api_key, new_session_token, verify_password
from hub.timeutil import utcnow
from hub.web.access import dumps

router = APIRouter(prefix="/admin/api")
COOKIE = "hub_session"


def ensure_bootstrap_admin() -> None:
    settings = get_settings()
    if not settings.admin_password:
        return
    session = session_for("admin")
    try:
        existing = session.scalars(select(AdminUser).where(AdminUser.email == settings.admin_email)).one_or_none()
        if existing is None:
            session.add(
                AdminUser(
                    email=settings.admin_email,
                    password_hash=hash_password(settings.admin_password),
                    role="platform_admin",
                    disabled=False,
                    created_at=utcnow(),
                )
            )
            session.commit()
    finally:
        session.close()


def _json(payload: dict, status: int = 200, headers: dict | None = None) -> Response:
    response = Response(content=dumps(payload), status_code=status, media_type="application/json", headers=headers)
    return response


def _error(status: int, code: str, message: str) -> Response:
    return _json({"error": {"code": code, "message": message}}, status)


def _admin(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None, None
    session = session_for("admin")
    row = session.scalars(select(AdminSession).where(AdminSession.token_hash == hash_secret(token))).one_or_none()
    if row is None or row.expires_at <= utcnow():
        session.close()
        return None, None
    user = session.get(AdminUser, row.admin_id)
    if user is None or user.disabled:
        session.close()
        return None, None
    return session, (user, row)


def _require(request: Request, roles: set[str]):
    session, found = _admin(request)
    if found is None:
        if session is not None:
            session.close()
        return None, None, _error(401, "UNAUTHORIZED", "Admin sign-in is required")
    user, row = found
    if user.role not in roles:
        session.close()
        return None, None, _error(403, "FORBIDDEN", "This admin role cannot perform that action")
    if request.method not in {"GET", "HEAD"} and request.headers.get("x-csrf-token") != row.csrf_token:
        session.close()
        return None, None, _error(403, "CSRF", "The CSRF token does not match this session")
    return session, user, None


def _audit(session, user: AdminUser, action: str, target: str, details: dict) -> None:
    session.add(
        AdminAuditLog(
            admin_id=user.id,
            action=action,
            target=target,
            details=details,
            created_at=utcnow(),
        )
    )


@router.post("/login")
def login(payload: dict):
    email = str(payload.get("email") or "")
    password = str(payload.get("password") or "")
    session = session_for("admin")
    try:
        user = session.scalars(select(AdminUser).where(AdminUser.email == email)).one_or_none()
        if user is None or user.disabled or not verify_password(password, user.password_hash):
            return _error(401, "UNAUTHORIZED", "Email or password is incorrect")
        token, digest, csrf = new_session_token()
        session.add(
            AdminSession(
                admin_id=user.id,
                token_hash=digest,
                csrf_token=csrf,
                expires_at=utcnow() + timedelta(hours=12),
                created_at=utcnow(),
            )
        )
        _audit(session, user, "login", user.email, {})
        session.commit()
        response = _json({"email": user.email, "role": user.role, "csrf_token": csrf})
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=get_settings().cookie_secure,
            max_age=12 * 3600,
            path="/",
        )
        return response
    finally:
        session.close()


@router.post("/logout")
def logout(request: Request):
    session, user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        token = request.cookies.get(COOKIE, "")
        row = session.scalars(select(AdminSession).where(AdminSession.token_hash == hash_secret(token))).one_or_none()
        if row is not None:
            session.delete(row)
        session.commit()
        response = _json({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response
    finally:
        session.close()


@router.get("/me")
def me(request: Request):
    session, user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        row = session.scalars(select(AdminSession).where(AdminSession.admin_id == user.id).order_by(AdminSession.created_at.desc())).first()
        return _json({"email": user.email, "role": user.role, "csrf_token": None if row is None else row.csrf_token})
    finally:
        session.close()


@router.get("/overview")
def overview(request: Request):
    session, _user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        settings = get_settings()
        states = {
            row.dataset: row
            for row in session.scalars(select(DatasetSyncState).where(DatasetSyncState.school_id == settings.school_id)).all()
        }
        datasets = []
        for spec in DATASETS.values():
            state = states.get(spec.name)
            datasets.append(
                {
                    "dataset": spec.name,
                    "blocked": spec.blocked,
                    "block_reason": spec.block_reason,
                    "status": "blocked" if spec.blocked else (None if state is None else state.status),
                    "last_success_at": None if state is None or state.last_success_at is None else state.last_success_at.isoformat(),
                    "last_attempt_at": None if state is None or state.last_attempt_at is None else state.last_attempt_at.isoformat(),
                    "last_attempt_status": None if state is None else state.last_attempt_status,
                    "record_count": None if state is None else state.record_count,
                    "error_summary": spec.block_reason if spec.blocked else (None if state is None else state.error_summary),
                }
            )
        check = session.scalars(
            select(ConnectionCheck).where(ConnectionCheck.school_id == settings.school_id).order_by(ConnectionCheck.checked_at.desc())
        ).first()
        return _json(
            {
                "school_timezone": settings.school_timezone,
                "school_timezone_confirmed": settings.school_timezone_confirmed,
                "retention_configured": settings.raw_retention_days is not None and settings.audit_retention_days is not None,
                "database": "mysql",
                "connection": None
                if check is None
                else {"ok": check.ok, "checked_at": check.checked_at.isoformat(), "detail": check.detail, "error_code": check.error_code},
                "datasets": datasets,
            }
        )
    finally:
        session.close()


@router.get("/jobs")
def jobs(request: Request):
    session, _user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        rows = session.scalars(select(SyncJob).where(SyncJob.school_id == get_settings().school_id).order_by(SyncJob.created_at.desc()).limit(100)).all()
        return _json({"data": [_job(row) for row in rows]})
    finally:
        session.close()


@router.get("/jobs/{job_id}")
def job_detail(job_id: UUID, request: Request):
    session, _user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        job = session.get(SyncJob, job_id)
        if job is None:
            return _error(404, "NOT_FOUND", "Job was not found")
        attempts = session.scalars(select(SyncAttempt).where(SyncAttempt.job_id == job.id).order_by(SyncAttempt.started_at)).all()
        issues = session.scalars(select(QualityIssue).where(QualityIssue.job_id == job.id)).all()
        return _json(
            {
                "job": _job(job),
                "attempts": [
                    {
                        "attempt_number": item.attempt_number,
                        "status": item.status,
                        "started_at": item.started_at.isoformat(),
                        "finished_at": None if item.finished_at is None else item.finished_at.isoformat(),
                        "error_code": item.error_code,
                        "error_summary": item.error_summary,
                        "counts": item.counts,
                    }
                    for item in attempts
                ],
                "quality_issues": [{"code": item.code, "message": item.message, "mb_id": item.mb_id} for item in issues],
            }
        )
    finally:
        session.close()


@router.post("/jobs")
def create_job(payload: dict, request: Request):
    session, user, error = _require(request, {"platform_admin", "data_operator"})
    if error is not None:
        return error
    try:
        dataset = str(payload.get("dataset") or "")
        if dataset not in DATASETS:
            return _error(400, "DATASET_UNKNOWN", "Unknown dataset")
        spec = DATASETS[dataset]
        scope = {"mode": payload.get("mode") or "full"}
        if spec.supports_dates:
            if not payload.get("date_from") or not payload.get("date_to"):
                return _error(400, "DATE_RANGE_REQUIRED", "Attendance sync requires date_from and date_to")
            scope["date_from"] = payload["date_from"]
            scope["date_to"] = payload["date_to"]
        key = str(payload.get("idempotency_key") or "")
        if not key:
            return _error(400, "IDEMPOTENCY_KEY_REQUIRED", "Send an idempotency_key so a double click cannot start two jobs")
        try:
            job = enqueue_job(
                session,
                dataset=dataset,
                trigger="manual",
                idempotency_key=key,
                scope=scope,
                requested_by=user.email,
            )
        except DatasetBlocked as exc:
            return _error(409, "DATASET_BLOCKED", exc.reason)
        _audit(session, user, "manual_sync", dataset, {"job_id": str(job.id), "scope": scope})
        session.commit()
        return _json({"job": _job(job)})
    finally:
        session.close()


@router.get("/clients")
def clients(request: Request):
    session, _user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        rows = session.scalars(select(ApiClient).order_by(ApiClient.created_at)).all()
        data = []
        for client in rows:
            scopes = session.scalars(select(ClientPermission.scope).where(ClientPermission.client_id == client.id)).all()
            credentials = session.scalars(select(ApiCredential).where(ApiCredential.client_id == client.id)).all()
            data.append({**_client(client), "scopes": list(scopes), "credentials": [_credential(item) for item in credentials]})
        return _json({"data": data, "available_scopes": list(SCOPES)})
    finally:
        session.close()


@router.post("/clients")
def create_client(payload: dict, request: Request):
    session, user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        name = str(payload.get("name") or "").strip()
        scopes = payload.get("scopes") or []
        if not name:
            return _error(400, "NAME_REQUIRED", "Client name is required")
        if not isinstance(scopes, list) or not scopes or any(scope not in SCOPES for scope in scopes):
            return _error(400, "SCOPE_INVALID", "Choose at least one known scope")
        try:
            rate = int(payload["requests_per_minute"])
            records = int(payload["records_per_day"])
            nbytes = int(payload["bytes_per_day"])
        except (KeyError, TypeError, ValueError):
            return _error(400, "QUOTA_REQUIRED", "requests_per_minute, records_per_day, and bytes_per_day are required")
        if rate < 1 or records < 1 or nbytes < 1:
            return _error(400, "QUOTA_REQUIRED", "A client without a positive quota stays disabled")
        client = ApiClient(
            name=name,
            enabled=True,
            requests_per_minute=rate,
            records_per_day=records,
            bytes_per_day=nbytes,
            created_at=utcnow(),
        )
        session.add(client)
        session.flush()
        for scope in scopes:
            session.add(ClientPermission(client_id=client.id, scope=scope))
        for item in payload.get("ranges") or []:
            if item.get("dimension") not in {"year_group", "class", "academic_year"} or not item.get("mb_id"):
                return _error(400, "RANGE_INVALID", "Ranges need a dimension and mb_id")
            session.add(ClientRange(client_id=client.id, dimension=item["dimension"], mb_id=str(item["mb_id"])))
        secret, public_id, digest = new_api_key()
        days = int(payload.get("credential_days") or 180)
        credential = ApiCredential(
            client_id=client.id,
            public_id=public_id,
            secret_hash=digest,
            expires_at=utcnow() + timedelta(days=days),
            created_at=utcnow(),
        )
        session.add(credential)
        _audit(session, user, "create_client", name, {"client_id": str(client.id), "scopes": scopes, "public_id": public_id})
        session.commit()
        return _json({"client": _client(client), "scopes": scopes, "credential": {**_credential(credential), "secret": secret}})
    finally:
        session.close()


@router.post("/clients/{client_id}/credentials")
def rotate_credential(client_id: UUID, payload: dict, request: Request):
    session, user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        client = session.get(ApiClient, client_id)
        if client is None:
            return _error(404, "NOT_FOUND", "Client was not found")
        secret, public_id, digest = new_api_key()
        days = int(payload.get("credential_days") or 180)
        credential = ApiCredential(
            client_id=client.id,
            public_id=public_id,
            secret_hash=digest,
            expires_at=utcnow() + timedelta(days=days),
            created_at=utcnow(),
        )
        session.add(credential)
        _audit(session, user, "create_credential", str(client.id), {"public_id": public_id})
        session.commit()
        return _json({"credential": {**_credential(credential), "secret": secret}})
    finally:
        session.close()


@router.post("/credentials/{credential_id}/revoke")
def revoke_credential(credential_id: UUID, request: Request):
    session, user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        credential = session.get(ApiCredential, credential_id)
        if credential is None:
            return _error(404, "NOT_FOUND", "Credential was not found")
        credential.revoked_at = utcnow()
        _audit(session, user, "revoke_credential", credential.public_id, {})
        session.commit()
        return _json({"credential": _credential(credential)})
    finally:
        session.close()


@router.post("/clients/{client_id}/enabled")
def set_enabled(client_id: UUID, payload: dict, request: Request):
    session, user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        client = session.get(ApiClient, client_id)
        if client is None:
            return _error(404, "NOT_FOUND", "Client was not found")
        client.enabled = bool(payload.get("enabled"))
        _audit(session, user, "set_client_enabled", str(client.id), {"enabled": client.enabled})
        session.commit()
        return _json({"client": _client(client)})
    finally:
        session.close()


@router.get("/audit")
def audit(request: Request, client_id: str | None = None):
    session, _user, error = _require(request, {"platform_admin", "auditor"})
    if error is not None:
        return error
    try:
        stmt = select(ApiRequestLog).order_by(ApiRequestLog.requested_at.desc()).limit(200)
        if client_id:
            stmt = stmt.where(ApiRequestLog.client_id == UUID(client_id))
        rows = session.scalars(stmt).all()
        return _json(
            {
                "data": [
                    {
                        "request_id": row.request_id,
                        "client_id": None if row.client_id is None else str(row.client_id),
                        "requested_at": row.requested_at.isoformat(),
                        "http_method": row.http_method,
                        "route_template": row.route_template,
                        "http_status": row.http_status,
                        "error_code": row.error_code,
                        "returned_record_count": row.returned_record_count,
                        "response_bytes": row.response_bytes,
                        "authenticated": row.authenticated,
                    }
                    for row in rows
                ]
            }
        )
    finally:
        session.close()


@router.get("/alerts")
def alerts(request: Request):
    session, _user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        rows = session.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(100)).all()
        return _json(
            {
                "data": [
                    {
                        "id": str(row.id),
                        "kind": row.kind,
                        "message": row.message,
                        "status": row.status,
                        "details": row.details,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in rows
                ]
            }
        )
    finally:
        session.close()


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: UUID, request: Request):
    session, user, error = _require(request, {"platform_admin", "auditor"})
    if error is not None:
        return error
    try:
        alert = session.get(Alert, alert_id)
        if alert is None:
            return _error(404, "NOT_FOUND", "Alert was not found")
        alert.status = "acked"
        alert.acked_at = utcnow()
        _audit(session, user, "ack_alert", str(alert.id), {"kind": alert.kind})
        session.commit()
        return _json({"ok": True})
    finally:
        session.close()


@router.get("/analytics/summary")
def analytics_summary(request: Request):
    session, _user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        from sqlalchemy import text

        school_id = get_settings().school_id
        student_count = session.scalar(
            text("SELECT COUNT(*) FROM hub_analytics.v_student_roster WHERE school_id = :school"),
            {"school": school_id},
        )
        attendance = session.execute(
            text(
                """
                SELECT business_date, attendance_type, standard_status, SUM(record_count) AS record_count
                FROM hub_analytics.v_attendance_daily_summary
                WHERE school_id = :school
                GROUP BY business_date, attendance_type, standard_status
                ORDER BY business_date, attendance_type, standard_status
                """
            ),
            {"school": school_id},
        ).all()
        return _json(
            {
                "student_count": int(student_count or 0),
                "attendance": [
                    {
                        "business_date": str(row.business_date),
                        "attendance_type": row.attendance_type,
                        "status": row.standard_status,
                        "record_count": int(row.record_count),
                    }
                    for row in attendance
                ],
                "attendance_rate_published": False,
            }
        )
    finally:
        session.close()


@router.get("/analytics/roster")
def analytics_roster(request: Request):
    session, _user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        from sqlalchemy import text

        rows = session.execute(
            text(
                """
                SELECT mb_id, student_id, first_name, last_name, standard_status, year_group_name
                FROM hub_analytics.v_student_roster
                WHERE school_id = :school
                ORDER BY mb_id
                LIMIT 200
                """
            ),
            {"school": get_settings().school_id},
        ).all()
        return _json(
            {
                "data": [
                    {
                        "mb_id": row.mb_id,
                        "student_id": row.student_id,
                        "first_name": row.first_name,
                        "last_name": row.last_name,
                        "status": row.standard_status,
                        "year_group_name": row.year_group_name,
                    }
                    for row in rows
                ]
            }
        )
    finally:
        session.close()


def _job(job: SyncJob) -> dict:
    return {
        "id": str(job.id),
        "dataset": job.dataset,
        "status": job.status,
        "trigger": job.trigger,
        "scope": job.scope,
        "requested_by": job.requested_by,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "error_summary": job.error_summary,
        "counts": job.counts,
        "created_at": job.created_at.isoformat(),
        "started_at": None if job.started_at is None else job.started_at.isoformat(),
        "finished_at": None if job.finished_at is None else job.finished_at.isoformat(),
    }


def _client(client: ApiClient) -> dict:
    return {
        "id": str(client.id),
        "name": client.name,
        "enabled": client.enabled,
        "requests_per_minute": client.requests_per_minute,
        "records_per_day": client.records_per_day,
        "bytes_per_day": client.bytes_per_day,
    }


def _credential(credential: ApiCredential) -> dict:
    return {
        "id": str(credential.id),
        "public_id": credential.public_id,
        "expires_at": credential.expires_at.isoformat(),
        "revoked_at": None if credential.revoked_at is None else credential.revoked_at.isoformat(),
    }
