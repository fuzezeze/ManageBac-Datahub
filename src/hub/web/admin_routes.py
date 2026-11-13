from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select

from hub.catalog import DATASETS, READ_APIS, SCOPES
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
    ApiUsageBucket,
    ClientFieldGrant,
    ClientPermission,
    ClientRange,
    ConnectionCheck,
    DatasetSyncState,
    QualityIssue,
    SyncAttempt,
    SyncJob,
    SyncSchedule,
)
from hub.reports import REPORTS, ReportError, list_reports, run_report
from hub.scheduler import ensure_schedules
from hub.secrets import hash_password, hash_secret, new_api_key, new_session_token, verify_password
from hub.timeutil import utcnow
from hub.web.access import dumps

router = APIRouter(prefix="/admin/api")
COOKIE = "hub_session"
SIGNED_IN = {"platform_admin", "data_operator", "auditor", "user"}
ACCOUNT_ROLES = {"platform_admin": "admin", "user": "user"}


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
    session, user, error = _require(request, SIGNED_IN)
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
    session, user, error = _require(request, SIGNED_IN)
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
            data.append({**_client(client), "scopes": list(scopes), "fields": _field_map(session, client.id), "credentials": [_credential(item) for item in credentials]})
        return _json({"data": data, "available_scopes": list(SCOPES), "apis": _read_api_catalog()})
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
        if not isinstance(scopes, list) or any(scope not in SCOPES for scope in scopes):
            return _error(400, "SCOPE_INVALID", "Choose at least one known scope")
        try:
            fields = _parse_fields(payload)
        except ValueError as exc:
            return _error(400, "FIELD_INVALID", str(exc))
        scopes = _merge_scopes(scopes, fields)
        if not scopes:
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
        if fields:
            _store_fields(session, client.id, fields)
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
        return _json({"client": _client(client), "scopes": scopes, "fields": fields or {}, "credential": {**_credential(credential), "secret": secret}})
    finally:
        session.close()


@router.patch("/clients/{client_id}")
def update_client(client_id: UUID, payload: dict, request: Request):
    session, user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        client = session.get(ApiClient, client_id)
        if client is None:
            return _error(404, "NOT_FOUND", "Client was not found")
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                return _error(400, "NAME_REQUIRED", "Client name is required")
            client.name = name
        if "enabled" in payload:
            client.enabled = bool(payload.get("enabled"))
        for key in ("requests_per_minute", "records_per_day", "bytes_per_day"):
            if key in payload:
                try:
                    value = int(payload[key])
                except (TypeError, ValueError):
                    return _error(400, "QUOTA_REQUIRED", "Quotas must be positive integers")
                if value < 1:
                    return _error(400, "QUOTA_REQUIRED", "Quotas must be positive integers")
                setattr(client, key, value)
        fields = None
        if "fields" in payload:
            try:
                fields = _parse_fields(payload)
            except ValueError as exc:
                return _error(400, "FIELD_INVALID", str(exc))
            _store_fields(session, client.id, fields)
        if "scopes" in payload or fields is not None:
            scopes = payload.get("scopes") if "scopes" in payload else []
            if not isinstance(scopes, list) or any(scope not in SCOPES for scope in scopes):
                return _error(400, "SCOPE_INVALID", "Choose at least one known scope")
            scopes = _merge_scopes(scopes, fields if fields is not None else _field_map(session, client.id))
            if not scopes:
                return _error(400, "SCOPE_INVALID", "Choose at least one known scope")
            session.query(ClientPermission).filter(ClientPermission.client_id == client.id).delete(synchronize_session=False)
            for scope in scopes:
                session.add(ClientPermission(client_id=client.id, scope=scope))
        _audit(session, user, "update_client", str(client.id), {"name": client.name})
        session.commit()
        scopes = session.scalars(select(ClientPermission.scope).where(ClientPermission.client_id == client.id)).all()
        return _json({"client": _client(client), "scopes": list(scopes), "fields": _field_map(session, client.id)})
    finally:
        session.close()


@router.delete("/clients/{client_id}")
def delete_client(client_id: UUID, request: Request):
    session, user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        client = session.get(ApiClient, client_id)
        if client is None:
            return _error(404, "NOT_FOUND", "Client was not found")
        session.query(ClientFieldGrant).filter(ClientFieldGrant.client_id == client.id).delete(synchronize_session=False)
        session.query(ClientPermission).filter(ClientPermission.client_id == client.id).delete(synchronize_session=False)
        session.query(ClientRange).filter(ClientRange.client_id == client.id).delete(synchronize_session=False)
        session.query(ApiCredential).filter(ApiCredential.client_id == client.id).delete(synchronize_session=False)
        _audit(session, user, "delete_client", client.name, {"client_id": str(client.id)})
        session.delete(client)
        session.commit()
        return _json({"ok": True})
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


@router.get("/users")
def users(request: Request):
    session, _user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        rows = session.scalars(select(AdminUser).order_by(AdminUser.created_at)).all()
        return _json({"data": [_account(row) for row in rows], "roles": ["admin", "user"]})
    finally:
        session.close()


@router.post("/users")
def create_user(payload: dict, request: Request):
    session, user, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        email = str(payload.get("email") or "").strip().lower()
        password = str(payload.get("password") or "")
        role = _account_role(payload.get("role"))
        if not email or "@" not in email:
            return _error(400, "EMAIL_REQUIRED", "A valid email is required")
        if len(password) < 8:
            return _error(400, "PASSWORD_REQUIRED", "Password must be at least 8 characters")
        if role is None:
            return _error(400, "ROLE_INVALID", "Role must be admin or user")
        if session.scalars(select(AdminUser).where(AdminUser.email == email)).one_or_none() is not None:
            return _error(409, "EMAIL_TAKEN", "That email is already signed in to this hub")
        account = AdminUser(email=email, password_hash=hash_password(password), role=role, disabled=False, created_at=utcnow())
        session.add(account)
        session.flush()
        _audit(session, user, "create_user", email, {"role": ACCOUNT_ROLES[role]})
        session.commit()
        return _json({"user": _account(account)})
    finally:
        session.close()


@router.patch("/users/{user_id}")
def update_user(user_id: UUID, payload: dict, request: Request):
    session, actor, error = _require(request, {"platform_admin"})
    if error is not None:
        return error
    try:
        account = session.get(AdminUser, user_id)
        if account is None:
            return _error(404, "NOT_FOUND", "User was not found")
        if "role" in payload:
            role = _account_role(payload.get("role"))
            if role is None:
                return _error(400, "ROLE_INVALID", "Role must be admin or user")
            if account.id == actor.id and role != "platform_admin":
                return _error(400, "ROLE_INVALID", "You cannot remove your own admin access")
            account.role = role
        if "disabled" in payload:
            disabled = bool(payload.get("disabled"))
            if account.id == actor.id and disabled:
                return _error(400, "ROLE_INVALID", "You cannot disable your own account")
            account.disabled = disabled
        if payload.get("password"):
            password = str(payload["password"])
            if len(password) < 8:
                return _error(400, "PASSWORD_REQUIRED", "Password must be at least 8 characters")
            account.password_hash = hash_password(password)
        _audit(session, actor, "update_user", account.email, {"role": ACCOUNT_ROLES.get(account.role, account.role), "disabled": account.disabled})
        session.commit()
        return _json({"user": _account(account)})
    finally:
        session.close()


@router.get("/schedules")
def schedules(request: Request):
    session, _user, error = _require(request, {"platform_admin", "data_operator", "auditor"})
    if error is not None:
        return error
    try:
        ensure_schedules(session)
        session.commit()
        rows = {row.dataset: row for row in session.scalars(select(SyncSchedule)).all()}
        data = []
        for spec in DATASETS.values():
            if not spec.scheduled:
                continue
            row = rows.get(spec.name)
            data.append(
                {
                    "dataset": spec.name,
                    "blocked": spec.blocked,
                    "supports_dates": spec.supports_dates,
                    "cadence": None if row is None else row.cadence,
                    "hour": None if row is None else row.hour,
                    "minute": 0 if row is None else row.minute,
                    "interval_minutes": None if row is None else row.interval_minutes,
                    "enabled": True if row is None else row.enabled,
                }
            )
        return _json({"data": data})
    finally:
        session.close()


@router.patch("/schedules/{dataset}")
def update_schedule(dataset: str, payload: dict, request: Request):
    session, user, error = _require(request, {"platform_admin", "data_operator"})
    if error is not None:
        return error
    try:
        spec = DATASETS.get(dataset)
        if spec is None or not spec.scheduled:
            return _error(404, "DATASET_UNKNOWN", "Unknown dataset")
        ensure_schedules(session)
        row = session.get(SyncSchedule, dataset)
        if row is None:
            return _error(404, "DATASET_UNKNOWN", "Unknown dataset")
        cadence = str(payload.get("cadence") or row.cadence)
        if cadence not in {"daily", "hourly", "interval"}:
            return _error(400, "CADENCE_INVALID", "Cadence must be daily, hourly, or interval")
        try:
            minute = int(payload.get("minute", row.minute))
            hour = payload.get("hour", row.hour)
            hour = None if hour in {None, ""} else int(hour)
            interval = payload.get("interval_minutes", row.interval_minutes)
            interval = None if interval in {None, ""} else int(interval)
        except (TypeError, ValueError):
            return _error(400, "CADENCE_INVALID", "Hour, minute, and interval must be numbers")
        if not 0 <= minute <= 59:
            return _error(400, "CADENCE_INVALID", "Minute must be between 0 and 59")
        if cadence == "daily" and (hour is None or not 0 <= hour <= 23):
            return _error(400, "CADENCE_INVALID", "A daily sync needs an hour from 0 to 23")
        if cadence == "interval" and (interval is None or not 5 <= interval <= 1440):
            return _error(400, "CADENCE_INVALID", "Interval must be between 5 and 1440 minutes")
        row.cadence = cadence
        row.hour = hour if cadence == "daily" else None
        row.minute = minute
        row.interval_minutes = interval if cadence == "interval" else None
        if "enabled" in payload:
            row.enabled = bool(payload.get("enabled"))
        _audit(session, user, "update_schedule", dataset, {"cadence": row.cadence, "hour": row.hour, "minute": row.minute, "interval_minutes": row.interval_minutes, "enabled": row.enabled})
        session.commit()
        return _json({"dataset": dataset, "cadence": row.cadence, "hour": row.hour, "minute": row.minute, "interval_minutes": row.interval_minutes, "enabled": row.enabled})
    finally:
        session.close()


@router.get("/usage")
def usage(request: Request):
    session, _user, error = _require(request, {"platform_admin", "auditor"})
    if error is not None:
        return error
    try:
        clients = {row.id: row.name for row in session.scalars(select(ApiClient)).all()}
        grouped = session.execute(
            select(
                ApiUsageBucket.route_template,
                func.coalesce(func.sum(ApiUsageBucket.request_count), 0),
                func.coalesce(func.sum(ApiUsageBucket.success_count), 0),
                func.coalesce(func.sum(ApiUsageBucket.error_count), 0),
                func.coalesce(func.sum(ApiUsageBucket.record_count), 0),
                func.coalesce(func.sum(ApiUsageBucket.byte_count), 0),
                func.max(ApiUsageBucket.bucket_start),
            )
            .where(ApiUsageBucket.bucket_size == "day")
            .group_by(ApiUsageBucket.route_template)
            .order_by(ApiUsageBucket.route_template)
        ).all()
        detailed = session.execute(
            select(
                ApiUsageBucket.client_id,
                ApiUsageBucket.route_template,
                func.coalesce(func.sum(ApiUsageBucket.request_count), 0),
                func.coalesce(func.sum(ApiUsageBucket.success_count), 0),
                func.coalesce(func.sum(ApiUsageBucket.error_count), 0),
                func.coalesce(func.sum(ApiUsageBucket.record_count), 0),
                func.max(ApiUsageBucket.bucket_start),
            )
            .where(ApiUsageBucket.bucket_size == "day")
            .group_by(ApiUsageBucket.client_id, ApiUsageBucket.route_template)
            .order_by(ApiUsageBucket.route_template)
        ).all()
        return _json(
            {
                "by_route": [
                    {
                        "route": row[0],
                        "request_count": int(row[1]),
                        "success_count": int(row[2]),
                        "error_count": int(row[3]),
                        "record_count": int(row[4]),
                        "byte_count": int(row[5]),
                        "last_bucket": None if row[6] is None else row[6].isoformat(),
                    }
                    for row in grouped
                ],
                "by_client": [
                    {
                        "client_id": str(row[0]),
                        "client_name": clients.get(row[0], ""),
                        "route": row[1],
                        "request_count": int(row[2]),
                        "success_count": int(row[3]),
                        "error_count": int(row[4]),
                        "record_count": int(row[5]),
                        "last_bucket": None if row[6] is None else row[6].isoformat(),
                    }
                    for row in detailed
                ],
            }
        )
    finally:
        session.close()


@router.get("/analytics/reports")
def analytics_reports(request: Request):
    session, _user, error = _require(request, SIGNED_IN)
    if error is not None:
        return error
    session.close()
    return _json({"reports": list_reports()})


@router.get("/analytics/reports/{report_key}")
def analytics_report(report_key: str, request: Request):
    session, _user, error = _require(request, SIGNED_IN)
    if error is not None:
        return error
    try:
        if report_key not in REPORTS:
            return _error(404, "REPORT_UNKNOWN", "Unknown report")
        try:
            return _json(run_report(session, report_key, dict(request.query_params)))
        except ReportError as exc:
            return _error(400, "REPORT_FILTER_INVALID", str(exc))
    finally:
        session.close()


@router.get("/analytics/summary")
def analytics_summary(request: Request):
    session, _user, error = _require(request, SIGNED_IN)
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


def _account_role(value) -> str | None:
    role = str(value or "").strip().lower()
    if role in {"admin", "platform_admin"}:
        return "platform_admin"
    if role == "user":
        return "user"
    return None


def _account(account: AdminUser) -> dict:
    return {
        "id": str(account.id),
        "email": account.email,
        "role": ACCOUNT_ROLES.get(account.role, account.role),
        "disabled": account.disabled,
    }


def _read_api_catalog() -> list[dict]:
    return [
        {"name": spec.name, "title": spec.title, "route": spec.route, "scope": spec.scope, "fields": list(spec.fields)}
        for spec in READ_APIS.values()
    ]


def _parse_fields(payload: dict) -> dict[str, list[str]] | None:
    if "fields" not in payload or payload.get("fields") is None:
        return None
    raw = payload.get("fields")
    if not isinstance(raw, dict):
        raise ValueError("fields must be an object of API name to field names")
    cleaned: dict[str, list[str]] = {}
    for resource, names in raw.items():
        spec = READ_APIS.get(str(resource))
        if spec is None:
            raise ValueError(f"Unknown API {resource}")
        if not isinstance(names, list):
            raise ValueError(f"{resource} fields must be a list")
        chosen = []
        for name in names:
            field_name = str(name)
            if field_name not in spec.fields:
                raise ValueError(f"Unknown field {field_name} on {resource}")
            if field_name not in chosen:
                chosen.append(field_name)
        if chosen:
            cleaned[spec.name] = chosen
    return cleaned


def _merge_scopes(scopes: list, fields: dict[str, list[str]] | None) -> list[str]:
    merged = [scope for scope in scopes if scope in SCOPES]
    if fields:
        for resource, names in fields.items():
            spec = READ_APIS[resource]
            if spec.scope not in merged:
                merged.append(spec.scope)
            if "email" in names and resource == "students" and "students:contact" not in merged:
                merged.append("students:contact")
            if "email" in names and resource == "teachers" and "teachers:contact" not in merged:
                merged.append("teachers:contact")
    return merged


def _store_fields(session, client_id, fields: dict[str, list[str]]) -> None:
    session.query(ClientFieldGrant).filter(ClientFieldGrant.client_id == client_id).delete(synchronize_session=False)
    for resource, names in fields.items():
        for name in names:
            session.add(ClientFieldGrant(client_id=client_id, resource=resource, field_name=name))


def _field_map(session, client_id) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    rows = session.scalars(select(ClientFieldGrant).where(ClientFieldGrant.client_id == client_id).order_by(ClientFieldGrant.field_name)).all()
    for row in rows:
        grouped.setdefault(row.resource, []).append(row.field_name)
    return grouped
