from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from hub.catalog import DATASETS, SCOPE_DATASETS
from hub.config import get_settings
from hub.db import session_for
from hub.models import (
    AcademicYear,
    AttendanceRecord,
    ClassGroup,
    CoverageWindow,
    DatasetSyncState,
    Student,
    StudentClass,
    StudentYearGroup,
    Subject,
    Teacher,
    TeacherClass,
    Term,
    YearGroup,
)
from hub.timeutil import utcnow
from hub.web.access import (
    ApiFailure,
    AuditPersistenceError,
    Principal,
    QuotaExceeded,
    auth_failure_limited,
    authenticate,
    clean_limit,
    decode_cursor,
    dumps,
    encode_cursor,
    error_payload,
    filter_hash,
    log_anonymous,
    new_request_id,
    persist_access,
)

router = APIRouter()


def _ip(request: Request) -> str | None:
    settings = get_settings()
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    if request.client:
        return request.client.host
    return None


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


def _route(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


def _fail(request_id: str, status: int, code: str, message: str, headers: dict | None = None) -> Response:
    return Response(content=error_payload(request_id, code, message), status_code=status, media_type="application/json", headers=headers)


def _principal(request: Request, request_id: str, api: Session) -> Principal:
    ip = _ip(request)
    if auth_failure_limited(api, ip):
        raise ApiFailure(429, "AUTH_RATE_LIMITED", "Too many failed authentication attempts")
    auth = session_for("auth")
    try:
        principal = authenticate(auth, _bearer(request))
    finally:
        auth.close()
    if principal is None:
        log_anonymous(
            api,
            request_id=request_id,
            method=request.method,
            route=_route(request),
            filters={},
            status=401,
            error_code="UNAUTHORIZED",
            returned_count=0,
            response_bytes=0,
            source_ip=ip,
            user_agent=request.headers.get("user-agent"),
            started=utcnow(),
        )
        raise ApiFailure(401, "UNAUTHORIZED", "Credential is missing, invalid, expired, or revoked")
    return principal


def _require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes and not (scope == "students:read" and "students:contact" in principal.scopes):
        if scope not in principal.scopes:
            raise ApiFailure(403, "FORBIDDEN", "The client is not allowed to read this resource")


def _finish(request: Request, api: Session, principal: Principal, request_id: str, started: datetime, filters: dict, payload: dict, count: int, status: int = 200, error_code: str | None = None) -> Response:
    body = dumps(payload)
    try:
        persist_access(
            api,
            principal=principal,
            request_id=request_id,
            method=request.method,
            route=_route(request),
            filters=filters,
            status=status,
            error_code=error_code,
            returned_count=count,
            body=body,
            source_ip=_ip(request),
            user_agent=request.headers.get("user-agent"),
            started=started,
        )
    except QuotaExceeded as exc:
        return _fail(request_id, 429, "QUOTA_EXCEEDED", "Request or volume quota exceeded", {"Retry-After": str(exc.retry_after)})
    except AuditPersistenceError:
        return _fail(request_id, 503, "AUDIT_UNAVAILABLE", "The request was not completed because the audit record could not be stored")
    return Response(content=body, media_type="application/json", status_code=status)


def _guard_dataset(api: Session, names: list[str], *, dates: list[date] | None = None) -> list[dict]:
    freshness = []
    blocked = False
    never = False
    stale = False
    incomplete = False
    settings = get_settings()
    now = utcnow()
    for name in names:
        spec = DATASETS.get(name)
        if spec and spec.blocked:
            freshness.append({"dataset": name, "status": "blocked", "detail": spec.block_reason})
            blocked = True
            continue
        state = api.scalars(select(DatasetSyncState).where(DatasetSyncState.school_id == settings.school_id, DatasetSyncState.dataset == name)).one_or_none()
        if state is None or state.last_success_at is None:
            freshness.append({"dataset": name, "status": "never"})
            never = True
            continue
        threshold = settings.attendance_freshness_hours if name.startswith("attendance") else settings.freshness_hours
        status = "fresh" if now - state.last_success_at <= timedelta(hours=threshold) else "stale"
        if state.last_attempt_status not in {None, "succeeded", "partial_failed"} and status == "fresh":
            status = "degraded"
        item = {
            "dataset": name,
            "status": status,
            "last_successful_sync_at": state.last_success_at.isoformat(),
            "last_attempt_status": state.last_attempt_status,
        }
        if dates and name.startswith("attendance"):
            present = set(
                api.scalars(
                    select(CoverageWindow.window_date).where(
                        CoverageWindow.school_id == settings.school_id,
                        CoverageWindow.dataset == name,
                        CoverageWindow.window_date >= dates[0],
                        CoverageWindow.window_date <= dates[-1],
                        CoverageWindow.complete.is_(True),
                    )
                ).all()
            )
            if any(day not in present for day in dates):
                item["status"] = "incomplete"
                item["coverage"] = "incomplete"
                incomplete = True
            else:
                item["coverage"] = "complete"
        if item["status"] == "stale":
            stale = True
        freshness.append(item)
    if blocked:
        raise ApiFailure(503, "DATASET_BLOCKED", "This dataset is not available from the source API")
    if never:
        raise ApiFailure(503, "DATA_NOT_READY", "This dataset has not completed a successful sync")
    if stale and settings.strict_freshness:
        raise ApiFailure(503, "STALE_DATA", "This dataset is older than the configured freshness threshold")
    if incomplete and settings.strict_freshness:
        raise ApiFailure(503, "COVERAGE_INCOMPLETE", "The requested attendance dates are not fully synced")
    return freshness


def _page(api: Session, stmt, columns, principal: Principal, digest: str, cursor: str | None, limit: int):
    if cursor:
        keys = decode_cursor(cursor, principal.client_id, digest)
        stmt = stmt.where(tuple_(*columns) > tuple_(*_coerce(columns, keys)))
    rows = api.execute(stmt.order_by(*columns).limit(limit + 1)).all()
    extra = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if extra and rows:
        last = rows[-1]
        next_cursor = encode_cursor(principal.client_id, digest, [str(getattr(last, column.key)) for column in columns])
    return rows, next_cursor


def _coerce(columns, keys: list[str]):
    values = []
    for column, key in zip(columns, keys, strict=True):
        if "id" == column.key and column.type.python_type is UUID or getattr(column.type, "as_uuid", False):
            values.append(UUID(key))
        elif column.key == "business_date":
            values.append(date.fromisoformat(key))
        else:
            values.append(key)
    return tuple(values)


def _student_filters(principal: Principal, year_group_id: str | None):
    filters = {"year_group_id": year_group_id, "ranges": sorted(principal.ranges.get("year_group", []))}
    return filters


def _apply_year_group_range(stmt, column, principal: Principal):
    allowed = principal.ranges.get("year_group")
    if allowed:
        stmt = stmt.where(column.in_(allowed))
    return stmt


def _apply_class_range(stmt, column, principal: Principal):
    allowed = principal.ranges.get("class")
    if allowed:
        stmt = stmt.where(column.in_(allowed))
    return stmt


def _student_payload(row, principal: Principal) -> dict:
    payload = {
        "id": str(row.id),
        "mb_id": row.mb_id,
        "student_id": row.student_id,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "other_name": row.other_name,
        "status": row.standard_status,
        "source_status": row.source_status,
        "program": row.program,
        "grade": row.grade,
        "year_group_id": row.year_group_mb_id,
    }
    if "students:contact" in principal.scopes:
        payload["email"] = row.email
    return payload


def _serve(request: Request, builder):
    request_id = new_request_id()
    started = utcnow()
    api = session_for("api")
    principal = None
    try:
        principal = _principal(request, request_id, api)
        return builder(api, principal, request_id, started)
    except ApiFailure as exc:
        if principal is not None:
            try:
                return _finish(
                    request,
                    api,
                    principal,
                    request_id,
                    started,
                    {},
                    {"error": {"request_id": request_id, "code": exc.code, "message": exc.message}},
                    0,
                    status=exc.status,
                    error_code=exc.code,
                )
            except ApiFailure:
                pass
        headers = {"Retry-After": "60"} if exc.status == 429 else None
        return _fail(request_id, exc.status, exc.code, exc.message, headers)
    finally:
        api.close()


@router.get("/api/v1/students")
def list_students(request: Request, limit: int | None = None, cursor: str | None = None, year_group_id: str | None = None):
    def build(api, principal, request_id, started):
        _require_scope(principal, "students:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, ["students"])
        filters = _student_filters(principal, year_group_id)
        digest = filter_hash(filters)
        stmt = select(
            Student.id, Student.mb_id, Student.student_id, Student.first_name, Student.last_name, Student.other_name,
            Student.standard_status, Student.source_status, Student.program, Student.grade, Student.year_group_mb_id, Student.email,
        ).where(Student.school_id == get_settings().school_id)
        if year_group_id:
            stmt = stmt.where(Student.year_group_mb_id == year_group_id)
        stmt = _apply_year_group_range(stmt, Student.year_group_mb_id, principal)
        rows, next_cursor = _page(api, stmt, (Student.mb_id, Student.id), principal, digest, cursor, page_size)
        data = [_student_payload(row, principal) for row in rows]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/students/{student_id}")
def get_student(student_id: UUID, request: Request):
    def build(api, principal, request_id, started):
        _require_scope(principal, "students:read")
        freshness = _guard_dataset(api, ["students"])
        stmt = select(
            Student.id, Student.mb_id, Student.student_id, Student.first_name, Student.last_name, Student.other_name,
            Student.standard_status, Student.source_status, Student.program, Student.grade, Student.year_group_mb_id, Student.email,
        ).where(Student.school_id == get_settings().school_id, Student.id == student_id)
        stmt = _apply_year_group_range(stmt, Student.year_group_mb_id, principal)
        row = api.execute(stmt).one_or_none()
        if row is None:
            raise ApiFailure(404, "NOT_FOUND", "Student was not found")
        return _finish(request, api, principal, request_id, started, {"id": str(student_id)}, {"data": _student_payload(row, principal), "meta": _meta(request_id, 1, None, freshness)}, 1)
    return _serve(request, build)


@router.get("/api/v1/teachers")
def list_teachers(request: Request, limit: int | None = None, cursor: str | None = None):
    def build(api, principal, request_id, started):
        _require_scope(principal, "teachers:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, ["teachers"])
        filters = {}
        digest = filter_hash(filters)
        stmt = select(
            Teacher.id, Teacher.mb_id, Teacher.identifier, Teacher.first_name, Teacher.last_name, Teacher.other_name,
            Teacher.standard_status, Teacher.source_status, Teacher.email,
        ).where(Teacher.school_id == get_settings().school_id)
        rows, next_cursor = _page(api, stmt, (Teacher.mb_id, Teacher.id), principal, digest, cursor, page_size)
        data = []
        for row in rows:
            item = {
                "id": str(row.id),
                "mb_id": row.mb_id,
                "identifier": row.identifier,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "other_name": row.other_name,
                "status": row.standard_status,
                "source_status": row.source_status,
            }
            if "teachers:contact" in principal.scopes:
                item["email"] = row.email
            data.append(item)
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/teachers/{teacher_id}")
def get_teacher(teacher_id: UUID, request: Request):
    def build(api, principal, request_id, started):
        _require_scope(principal, "teachers:read")
        freshness = _guard_dataset(api, ["teachers"])
        row = api.execute(
            select(
                Teacher.id, Teacher.mb_id, Teacher.identifier, Teacher.first_name, Teacher.last_name, Teacher.other_name,
                Teacher.standard_status, Teacher.source_status, Teacher.email,
            ).where(Teacher.school_id == get_settings().school_id, Teacher.id == teacher_id)
        ).one_or_none()
        if row is None:
            raise ApiFailure(404, "NOT_FOUND", "Teacher was not found")
        item = {
            "id": str(row.id),
            "mb_id": row.mb_id,
            "identifier": row.identifier,
            "first_name": row.first_name,
            "last_name": row.last_name,
            "status": row.standard_status,
        }
        if "teachers:contact" in principal.scopes:
            item["email"] = row.email
        return _finish(request, api, principal, request_id, started, {"id": str(teacher_id)}, {"data": item, "meta": _meta(request_id, 1, None, freshness)}, 1)
    return _serve(request, build)


@router.get("/api/v1/academic-years")
def list_years(request: Request, limit: int | None = None, cursor: str | None = None):
    return _simple_academics(request, "academic_years", AcademicYear, limit, cursor, lambda row: {
        "id": str(row.id), "mb_id": row.mb_id, "program_code": row.program_code, "name": row.name,
        "starts_on": row.starts_on, "ends_on": row.ends_on, "active": row.active,
    })


@router.get("/api/v1/terms")
def list_terms(request: Request, limit: int | None = None, cursor: str | None = None):
    return _simple_academics(request, "terms", Term, limit, cursor, lambda row: {
        "id": str(row.id), "mb_id": row.mb_id, "academic_year_id": row.academic_year_mb_id, "program_code": row.program_code,
        "name": row.name, "starts_on": row.starts_on, "ends_on": row.ends_on, "active": row.active, "exam_grade": row.exam_grade,
    })


@router.get("/api/v1/subjects")
def list_subjects(request: Request, limit: int | None = None, cursor: str | None = None):
    return _simple_academics(request, "subjects", Subject, limit, cursor, lambda row: {
        "id": str(row.id), "mb_id": row.mb_id, "program_code": row.program_code, "name": row.name, "code": row.code,
    }, dataset="subjects")


@router.get("/api/v1/courses")
def list_courses(request: Request):
    def build(api, principal, request_id, started):
        _require_scope(principal, "academics:read")
        _guard_dataset(api, ["courses"])
        return _finish(request, api, principal, request_id, started, {}, {"data": [], "meta": {}}, 0)
    return _serve(request, build)


def _simple_academics(request, label, model, limit, cursor, projector, dataset="academic_years"):
    def build(api, principal, request_id, started):
        _require_scope(principal, "academics:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, [dataset])
        filters = {}
        digest = filter_hash(filters)
        columns = [model.mb_id, model.id]
        if hasattr(model, "program_code") and label != "terms":
            columns = [model.program_code, model.mb_id, model.id]
        stmt = select(model).where(model.school_id == get_settings().school_id)
        rows, next_cursor = _page(api, stmt, tuple(columns), principal, digest, cursor, page_size)
        data = [projector(row) for row in rows]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/classes")
def list_classes(request: Request, limit: int | None = None, cursor: str | None = None):
    def build(api, principal, request_id, started):
        _require_scope(principal, "classes:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, ["classes"])
        filters = {"ranges": sorted(principal.ranges.get("class", []))}
        digest = filter_hash(filters)
        stmt = select(ClassGroup).where(ClassGroup.school_id == get_settings().school_id)
        stmt = _apply_class_range(stmt, ClassGroup.mb_id, principal)
        rows, next_cursor = _page(api, stmt, (ClassGroup.mb_id, ClassGroup.id), principal, digest, cursor, page_size)
        data = [_class_payload(row) for row in rows]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/classes/{class_id}")
def get_class(class_id: UUID, request: Request):
    def build(api, principal, request_id, started):
        _require_scope(principal, "classes:read")
        freshness = _guard_dataset(api, ["classes"])
        stmt = select(ClassGroup).where(ClassGroup.school_id == get_settings().school_id, ClassGroup.id == class_id)
        stmt = _apply_class_range(stmt, ClassGroup.mb_id, principal)
        row = api.scalars(stmt).one_or_none()
        if row is None:
            raise ApiFailure(404, "NOT_FOUND", "Class was not found")
        return _finish(request, api, principal, request_id, started, {"id": str(class_id)}, {"data": _class_payload(row), "meta": _meta(request_id, 1, None, freshness)}, 1)
    return _serve(request, build)


def _class_payload(row) -> dict:
    return {
        "id": str(row.id),
        "mb_id": row.mb_id,
        "name": row.name,
        "uniq_id": row.uniq_id,
        "program": row.program,
        "grade": row.grade,
        "subject_id": row.subject_mb_id,
        "subject_name": row.subject_name,
        "start_term_id": row.start_term_mb_id,
        "end_term_id": row.end_term_mb_id,
        "archived": row.archived,
    }


@router.get("/api/v1/classes/{class_id}/students")
def class_students(class_id: UUID, request: Request, limit: int | None = None, cursor: str | None = None):
    def build(api, principal, request_id, started):
        _require_scope(principal, "enrollments:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, ["student_classes"])
        klass = api.get(ClassGroup, class_id)
        if klass is None or klass.school_id != get_settings().school_id:
            raise ApiFailure(404, "NOT_FOUND", "Class was not found")
        if principal.ranges.get("class") and klass.mb_id not in principal.ranges["class"]:
            raise ApiFailure(404, "NOT_FOUND", "Class was not found")
        filters = {"class_id": str(class_id)}
        digest = filter_hash(filters)
        stmt = select(StudentClass).where(
            StudentClass.school_id == klass.school_id,
            StudentClass.class_mb_id == klass.mb_id,
            StudentClass.member_status == "active",
        )
        rows, next_cursor = _page(api, stmt, (StudentClass.student_mb_id, StudentClass.id), principal, digest, cursor, page_size)
        data = [
            {"class_id": str(klass.id), "class_mb_id": klass.mb_id, "student_mb_id": row.student_mb_id, "level": row.level, "member_status": row.member_status}
            for row in rows
        ]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/classes/{class_id}/teachers")
def class_teachers(class_id: UUID, request: Request, limit: int | None = None, cursor: str | None = None):
    def build(api, principal, request_id, started):
        _require_scope(principal, "teaching-assignments:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, ["teacher_classes"])
        klass = api.get(ClassGroup, class_id)
        if klass is None or klass.school_id != get_settings().school_id:
            raise ApiFailure(404, "NOT_FOUND", "Class was not found")
        if principal.ranges.get("class") and klass.mb_id not in principal.ranges["class"]:
            raise ApiFailure(404, "NOT_FOUND", "Class was not found")
        filters = {"class_id": str(class_id)}
        digest = filter_hash(filters)
        stmt = select(TeacherClass).where(
            TeacherClass.school_id == klass.school_id,
            TeacherClass.class_mb_id == klass.mb_id,
            TeacherClass.member_status == "active",
        )
        rows, next_cursor = _page(api, stmt, (TeacherClass.teacher_mb_id, TeacherClass.id), principal, digest, cursor, page_size)
        data = [
            {
                "class_id": str(klass.id),
                "class_mb_id": klass.mb_id,
                "teacher_mb_id": row.teacher_mb_id,
                "role": row.role,
                "show_on_reports": row.show_on_reports,
                "member_status": row.member_status,
            }
            for row in rows
        ]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/year-groups")
def list_year_groups(request: Request, limit: int | None = None, cursor: str | None = None):
    def build(api, principal, request_id, started):
        _require_scope(principal, "year-groups:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, ["year_groups"])
        filters = {"ranges": sorted(principal.ranges.get("year_group", []))}
        digest = filter_hash(filters)
        stmt = select(YearGroup).where(YearGroup.school_id == get_settings().school_id)
        stmt = _apply_year_group_range(stmt, YearGroup.mb_id, principal)
        rows, next_cursor = _page(api, stmt, (YearGroup.mb_id, YearGroup.id), principal, digest, cursor, page_size)
        data = [
            {"id": str(row.id), "mb_id": row.mb_id, "name": row.name, "program": row.program, "program_code": row.program_code, "grade": row.grade, "archived": row.archived}
            for row in rows
        ]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/year-groups/{year_group_id}/students")
def year_group_students(year_group_id: UUID, request: Request, limit: int | None = None, cursor: str | None = None):
    def build(api, principal, request_id, started):
        _require_scope(principal, "year-groups:read")
        page_size = clean_limit(limit)
        freshness = _guard_dataset(api, ["student_year_groups"])
        group = api.get(YearGroup, year_group_id)
        if group is None or group.school_id != get_settings().school_id:
            raise ApiFailure(404, "NOT_FOUND", "Year group was not found")
        if principal.ranges.get("year_group") and group.mb_id not in principal.ranges["year_group"]:
            raise ApiFailure(404, "NOT_FOUND", "Year group was not found")
        filters = {"year_group_id": str(year_group_id)}
        digest = filter_hash(filters)
        stmt = select(StudentYearGroup).where(
            StudentYearGroup.school_id == group.school_id,
            StudentYearGroup.year_group_mb_id == group.mb_id,
            StudentYearGroup.member_status == "active",
        )
        rows, next_cursor = _page(api, stmt, (StudentYearGroup.student_mb_id, StudentYearGroup.id), principal, digest, cursor, page_size)
        data = [{"year_group_id": str(group.id), "year_group_mb_id": group.mb_id, "student_mb_id": row.student_mb_id, "member_status": row.member_status} for row in rows]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/attendance")
def list_attendance(
    request: Request,
    date_from: str,
    date_to: str,
    attendance_type: str | None = None,
    student_id: UUID | None = None,
    class_id: UUID | None = None,
    year_group_id: UUID | None = None,
    limit: int | None = None,
    cursor: str | None = None,
):
    def build(api, principal, request_id, started):
        _require_scope(principal, "attendance:read")
        page_size = clean_limit(limit)
        try:
            start = date.fromisoformat(date_from)
            end = date.fromisoformat(date_to)
        except ValueError as exc:
            raise ApiFailure(400, "DATE_RANGE_INVALID", "date_from and date_to must be YYYY-MM-DD") from exc
        if end < start:
            raise ApiFailure(400, "DATE_RANGE_INVALID", "date_to is before date_from")
        span = (end - start).days + 1
        if span > get_settings().attendance_max_days:
            raise ApiFailure(400, "DATE_RANGE_INVALID", f"Attendance queries are limited to {get_settings().attendance_max_days} days")
        dates = [start + timedelta(days=offset) for offset in range(span)]
        datasets = []
        if attendance_type in {None, "class"}:
            datasets.append("attendance_class")
        if attendance_type in {None, "homeroom"}:
            datasets.append("attendance_homeroom")
        if attendance_type not in {None, "class", "homeroom"}:
            raise ApiFailure(400, "ATTENDANCE_TYPE_INVALID", "attendance_type must be class or homeroom")
        freshness = _guard_dataset(api, datasets, dates=dates)
        filters = {
            "date_from": date_from,
            "date_to": date_to,
            "attendance_type": attendance_type,
            "student_id": str(student_id) if student_id else None,
            "class_id": str(class_id) if class_id else None,
            "year_group_id": str(year_group_id) if year_group_id else None,
        }
        digest = filter_hash(filters)
        stmt = select(
            AttendanceRecord.id,
            AttendanceRecord.attendance_type,
            AttendanceRecord.student_mb_id,
            AttendanceRecord.business_date,
            AttendanceRecord.class_mb_id,
            AttendanceRecord.year_group_mb_id,
            AttendanceRecord.period,
            AttendanceRecord.source_status,
            AttendanceRecord.standard_status,
            AttendanceRecord.category,
        ).where(
            AttendanceRecord.school_id == get_settings().school_id,
            AttendanceRecord.business_date >= start,
            AttendanceRecord.business_date <= end,
        )
        if attendance_type:
            stmt = stmt.where(AttendanceRecord.attendance_type == attendance_type)
        if student_id:
            student = api.get(Student, student_id)
            if student is None:
                raise ApiFailure(404, "NOT_FOUND", "Student was not found")
            stmt = stmt.where(AttendanceRecord.student_mb_id == student.mb_id)
        if class_id:
            klass = api.get(ClassGroup, class_id)
            if klass is None:
                raise ApiFailure(404, "NOT_FOUND", "Class was not found")
            stmt = stmt.where(AttendanceRecord.class_mb_id == klass.mb_id)
        if year_group_id:
            group = api.get(YearGroup, year_group_id)
            if group is None:
                raise ApiFailure(404, "NOT_FOUND", "Year group was not found")
            stmt = stmt.where(AttendanceRecord.year_group_mb_id == group.mb_id)
        stmt = _apply_class_range(stmt, AttendanceRecord.class_mb_id, principal)
        stmt = _apply_year_group_range(stmt, AttendanceRecord.year_group_mb_id, principal)
        rows, next_cursor = _page(
            api,
            stmt,
            (AttendanceRecord.business_date, AttendanceRecord.student_mb_id, AttendanceRecord.period, AttendanceRecord.id),
            principal,
            digest,
            cursor,
            page_size,
        )
        data = [
            {
                "id": str(row.id),
                "attendance_type": row.attendance_type,
                "student_mb_id": row.student_mb_id,
                "business_date": row.business_date.isoformat(),
                "class_mb_id": row.class_mb_id,
                "year_group_mb_id": row.year_group_mb_id,
                "period": row.period,
                "source_status": row.source_status,
                "status": row.standard_status,
                "category": row.category,
            }
            for row in rows
        ]
        return _finish(request, api, principal, request_id, started, filters, {"data": data, "meta": _meta(request_id, len(data), next_cursor, freshness)}, len(data))
    return _serve(request, build)


@router.get("/api/v1/data-status")
def data_status(request: Request):
    def build(api, principal, request_id, started):
        names = []
        for scope, datasets in SCOPE_DATASETS.items():
            if scope in principal.scopes:
                names.extend(datasets)
        ordered = []
        for name in names:
            if name not in ordered:
                ordered.append(name)
        if not ordered:
            raise ApiFailure(403, "FORBIDDEN", "The client has no dataset permissions")
        freshness = []
        for name in ordered:
            spec = DATASETS[name]
            state = api.scalars(select(DatasetSyncState).where(DatasetSyncState.school_id == get_settings().school_id, DatasetSyncState.dataset == name)).one_or_none()
            if spec.blocked:
                freshness.append({"dataset": name, "status": "blocked", "detail": spec.block_reason})
            elif state is None or state.last_success_at is None:
                freshness.append({"dataset": name, "status": "never", "last_attempt_status": None if state is None else state.last_attempt_status})
            else:
                freshness.append(
                    {
                        "dataset": name,
                        "status": state.status,
                        "last_successful_sync_at": state.last_success_at.isoformat(),
                        "last_attempt_at": None if state.last_attempt_at is None else state.last_attempt_at.isoformat(),
                        "last_attempt_status": state.last_attempt_status,
                        "record_count": state.record_count,
                    }
                )
        return _finish(request, api, principal, request_id, started, {}, {"data": freshness, "meta": _meta(request_id, len(freshness), None, [])}, len(freshness))
    return _serve(request, build)


def _meta(request_id: str, count: int, next_cursor: str | None, freshness: list[dict]) -> dict:
    return {"request_id": request_id, "returned_count": count, "next_cursor": next_cursor, "freshness": freshness}
