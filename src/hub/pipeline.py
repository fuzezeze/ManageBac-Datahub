import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hub.catalog import DATASETS
from hub.config import get_settings
from hub.connector import AuthError, ContractError, ManageBacClient, ManageBacError, Page, RateLimited, TransientError
from hub.models import (
    ClassGroup,
    ConnectionCheck,
    DatasetSyncState,
    Student,
    StudentClass,
    StudentYearGroup,
    Teacher,
    TeacherClass,
    YearGroup,
)
from hub.normalize import (
    ShapeError,
    advisor_rows,
    iter_academic_years,
    iter_subjects,
    parse_class_attendance,
    parse_homeroom_attendance,
    student_ids_from_membership,
    teacher_refs_from_class,
)
from hub.publish import (
    LeaseLost,
    add_quality,
    apply_attendance,
    assert_lease,
    extend_lease,
    load_checkpoint,
    mark_coverage,
    mark_people_missing,
    replace_advisors,
    save_checkpoint,
    save_raw,
    upsert_class,
    upsert_student,
    upsert_student_class,
    upsert_student_year_group,
    upsert_subject,
    upsert_teacher,
    upsert_teacher_class,
    upsert_term,
    upsert_year,
    upsert_year_group,
    write_state,
)
from hub.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    archived: int = 0
    errors: int = 0
    fetched: int = 0

    def add(self, kind: str) -> None:
        setattr(self, kind, getattr(self, kind) + 1)

    def as_dict(self) -> dict:
        return asdict(self)


def run_dataset(session: Session, client: ManageBacClient, job, worker_id: str) -> Counts:
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
        raise ContractError(spec.block_reason)
    _require_dependencies(session, job)
    settings = get_settings()
    extend_lease(session, job.id, worker_id, settings.worker_lease_seconds)
    handlers = {
        "connection_check": _connection_check,
        "academic_years": _academic_years,
        "subjects": _subjects,
        "teachers": _teachers,
        "students": _students,
        "year_groups": _year_groups,
        "classes": _classes,
        "student_year_groups": _student_year_groups,
        "student_classes": _student_classes,
        "teacher_classes": _teacher_classes,
        "attendance_class": _attendance_class,
        "attendance_homeroom": _attendance_homeroom,
    }
    return handlers[job.dataset](session, client, job, worker_id)


def _require_dependencies(session: Session, job) -> None:
    for name in DATASETS[job.dataset].depends_on:
        state = session.scalars(
            select(DatasetSyncState).where(DatasetSyncState.school_id == job.school_id, DatasetSyncState.dataset == name)
        ).one_or_none()
        if state is not None and state.last_success_at is not None:
            continue
        if state is not None and state.status in {"failed", "blocked"}:
            raise ContractError(f"{job.dataset} is waiting on {name}, which is {state.status}")
        raise _DependencyWait(name)


class _DependencyWait(Exception):
    def __init__(self, dataset: str):
        super().__init__(dataset)
        self.dataset = dataset


def _store_pages(session: Session, job, pages: list[Page]) -> None:
    for page in pages:
        save_raw(session, school_id=job.school_id, job_id=job.id, page=page)


def _bump_from(counts: Counts, kind: str) -> None:
    if kind in {"created", "updated", "unchanged"}:
        counts.add(kind)


def _academic_years(session: Session, client: ManageBacClient, job, worker_id: str) -> Counts:
    page = client.get_page("school/academic-years")
    _store_pages(session, job, [page])
    parsed = iter_academic_years(page.payload)
    counts = Counts(fetched=page.record_count)
    now = utcnow()
    assert_lease(session, job.id, worker_id)
    for item in parsed:
        _bump_from(counts, upsert_year(session, item["program_code"], item["year"], school_id=job.school_id, job_id=job.id, now=now))
        for term in item["terms"]:
            if "id" not in term:
                raise ShapeError("academic term missing id")
            _bump_from(
                counts,
                upsert_term(session, item["program_code"], str(item["year"]["id"]), term, school_id=job.school_id, job_id=job.id, now=now),
            )
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _subjects(session: Session, client: ManageBacClient, job, worker_id: str) -> Counts:
    # Live /school/subjects is one object keyed by program and has no pagination meta.
    page = client.get_page("school/subjects")
    _store_pages(session, job, [page])
    parsed = iter_subjects(page.payload)
    counts = Counts(fetched=len(parsed))
    now = utcnow()
    assert_lease(session, job.id, worker_id)
    for item in parsed:
        _bump_from(counts, upsert_subject(session, item["program_code"], item["subject"], school_id=job.school_id, job_id=job.id, now=now))
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _people(session, client, job, worker_id: str, *, path: str, collection: str, model, upsert, dataset: str) -> Counts:
    mode = job.scope.get("mode") or "full"
    checkpoint = load_checkpoint(session, school_id=job.school_id, dataset=dataset, scope_key="incremental")
    pages: list[Page] = []
    deleted_pages: list[Page] = []
    upper = utcnow()
    if mode == "incremental":
        if not checkpoint.get("upper_bound"):
            raise ContractError("incremental sync requires a completed full sync")
        previous = datetime.fromisoformat(checkpoint["upper_bound"])
        since = (previous - timedelta(hours=6)).isoformat()
        pages = list(client.paginate(path, {"modified_since": since}, collection))
        deleted_pages = list(client.paginate(path, {"deleted_since": since}, collection))
    else:
        pages = list(client.paginate(path, {}, collection))
    _store_pages(session, job, pages + deleted_pages)
    counts = Counts(fetched=sum(page.record_count for page in pages + deleted_pages))
    now = utcnow()
    seen: set[str] = set()
    assert_lease(session, job.id, worker_id)
    for page in pages:
        for payload in page.payload[collection]:
            if not isinstance(payload, dict) or "id" not in payload:
                raise ShapeError(f"{collection} entry missing id")
            seen.add(str(payload["id"]))
            _bump_from(counts, upsert(session, payload, school_id=job.school_id, job_id=job.id, now=now))
    for page in deleted_pages:
        for payload in page.payload[collection]:
            if not isinstance(payload, dict) or "id" not in payload:
                raise ShapeError(f"deleted {collection} entry missing id")
            deleted_payload = dict(payload)
            deleted_payload.setdefault("archived", True)
            deleted_payload.setdefault("status", "deleted")
            seen.add(str(payload["id"]))
            _bump_from(counts, upsert(session, deleted_payload, school_id=job.school_id, job_id=job.id, now=now))
    if mode != "incremental":
        counts.archived += mark_people_missing(
            session, model, school_id=job.school_id, seen=seen, job_id=job.id, dataset=dataset, now=now
        )
    save_checkpoint(
        session,
        school_id=job.school_id,
        dataset=dataset,
        scope_key="incremental",
        cursor={"upper_bound": upper.isoformat()},
    )
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _teachers(session, client, job, worker_id: str) -> Counts:
    return _people(session, client, job, worker_id, path="teachers", collection="teachers", model=Teacher, upsert=upsert_teacher, dataset="teachers")


def _students(session, client, job, worker_id: str) -> Counts:
    return _people(session, client, job, worker_id, path="students", collection="students", model=Student, upsert=upsert_student, dataset="students")


def _year_groups(session, client, job, worker_id: str) -> Counts:
    pages = list(client.paginate("year-groups", {}, "year_groups"))
    _store_pages(session, job, pages)
    counts = Counts(fetched=sum(page.record_count for page in pages))
    now = utcnow()
    assert_lease(session, job.id, worker_id)
    group_ids: list[str] = []
    for page in pages:
        for payload in page.payload["year_groups"]:
            if not isinstance(payload, dict) or "id" not in payload:
                raise ShapeError("year group missing id")
            group_ids.append(str(payload["id"]))
            _bump_from(counts, upsert_year_group(session, payload, school_id=job.school_id, job_id=job.id, now=now))
    for group_id in group_ids:
        try:
            advisor_page = client.get_page(f"year-groups/{group_id}/advisors")
        except ContractError as exc:
            counts.errors += 1
            add_quality(session, school_id=job.school_id, job_id=job.id, dataset="year_groups", code="advisors_unavailable", message=str(exc), mb_id=group_id)
            continue
        _store_pages(session, job, [advisor_page])
        try:
            advisors = advisor_rows(advisor_page.payload)
        except ShapeError as exc:
            counts.errors += 1
            add_quality(session, school_id=job.school_id, job_id=job.id, dataset="year_groups", code="advisors_unparsed", message=str(exc), mb_id=group_id)
            continue
        replace_advisors(session, school_id=job.school_id, year_group_mb_id=group_id, advisors=advisors, job_id=job.id, now=now)
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _classes(session, client, job, worker_id: str) -> Counts:
    pages = list(client.paginate("classes", {}, "classes"))
    _store_pages(session, job, pages)
    counts = Counts(fetched=sum(page.record_count for page in pages))
    now = utcnow()
    assert_lease(session, job.id, worker_id)
    for page in pages:
        for payload in page.payload["classes"]:
            if not isinstance(payload, dict) or "id" not in payload:
                raise ShapeError("class missing id")
            refs = teacher_refs_from_class(payload)
            _bump_from(counts, upsert_class(session, payload, school_id=job.school_id, job_id=job.id, now=now, teacher_refs=refs))
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _student_year_groups(session, client, job, worker_id: str) -> Counts:
    groups = session.scalars(select(YearGroup).where(YearGroup.school_id == job.school_id)).all()
    counts = Counts()
    memberships: list[tuple[str, list[str]]] = []
    for group in groups:
        pages = list(client.paginate(f"year-groups/{group.mb_id}/students", {}, "students"))
        _store_pages(session, job, pages)
        ids = []
        for page in pages:
            counts.fetched += page.record_count
            for student_id, _level in student_ids_from_membership(page.payload):
                ids.append(student_id)
        memberships.append((group.mb_id, ids))
    now = utcnow()
    assert_lease(session, job.id, worker_id)
    for group_id, student_ids in memberships:
        seen = set(student_ids)
        for student_id in seen:
            _bump_from(
                counts,
                upsert_student_year_group(
                    session,
                    school_id=job.school_id,
                    student_mb_id=student_id,
                    year_group_mb_id=group_id,
                    member_status="active",
                    job_id=job.id,
                    now=now,
                ),
            )
        existing = session.scalars(
            select(StudentYearGroup).where(
                StudentYearGroup.school_id == job.school_id,
                StudentYearGroup.year_group_mb_id == group_id,
                StudentYearGroup.member_status == "active",
            )
        ).all()
        for row in existing:
            if row.student_mb_id not in seen:
                _bump_from(
                    counts,
                    upsert_student_year_group(
                        session,
                        school_id=job.school_id,
                        student_mb_id=row.student_mb_id,
                        year_group_mb_id=group_id,
                        member_status="removed",
                        job_id=job.id,
                        now=now,
                    ),
                )
                counts.archived += 1
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _listed(client: ManageBacClient, path: str, params: dict | None = None) -> list[Page]:
    """Follow meta.total_pages when present. A membership payload without meta is one page."""
    pages: list[Page] = []
    page_number = 1
    base = dict(params or {})
    while page_number <= 10000:
        page = client.get_page(path, {**base, "page": page_number, "per_page": 100})
        pages.append(page)
        meta = page.payload.get("meta")
        if not isinstance(meta, dict):
            return pages
        total_pages = int(meta.get("total_pages") or 1)
        next_page = meta.get("next_page")
        if next_page:
            page_number = int(next_page)
        elif page_number < total_pages:
            page_number += 1
        else:
            return pages
    raise ContractError("pagination exceeded 10000 pages")


def _student_classes(session, client, job, worker_id: str) -> Counts:
    classes = session.scalars(select(ClassGroup).where(ClassGroup.school_id == job.school_id)).all()
    counts = Counts()
    collected: list[tuple[str, list[tuple[str, str | None]]]] = []
    for klass in classes:
        pages = _listed(client, f"classes/{klass.mb_id}/students")
        _store_pages(session, job, pages)
        members: list[tuple[str, str | None]] = []
        for page in pages:
            found = student_ids_from_membership(page.payload)
            counts.fetched += len(found)
            members.extend(found)
        collected.append((klass.mb_id, members))
    now = utcnow()
    assert_lease(session, job.id, worker_id)
    for class_id, members in collected:
        seen = {student_id for student_id, _level in members}
        for student_id, level in members:
            _bump_from(
                counts,
                upsert_student_class(
                    session,
                    school_id=job.school_id,
                    student_mb_id=student_id,
                    class_mb_id=class_id,
                    level=level,
                    member_status="active",
                    job_id=job.id,
                    now=now,
                ),
            )
        existing = session.scalars(
            select(StudentClass).where(
                StudentClass.school_id == job.school_id,
                StudentClass.class_mb_id == class_id,
                StudentClass.member_status == "active",
            )
        ).all()
        for row in existing:
            if row.student_mb_id not in seen:
                upsert_student_class(
                    session,
                    school_id=job.school_id,
                    student_mb_id=row.student_mb_id,
                    class_mb_id=class_id,
                    level=row.level,
                    member_status="removed",
                    job_id=job.id,
                    now=now,
                )
                counts.archived += 1
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _teacher_classes(session, client, job, worker_id: str) -> Counts:
    classes = session.scalars(select(ClassGroup).where(ClassGroup.school_id == job.school_id)).all()
    if any(klass.teacher_refs is None for klass in classes):
        raise ContractError("one or more classes did not include a teacher list; refusing to infer removals")
    counts = Counts(fetched=sum(len(klass.teacher_refs or []) for klass in classes))
    now = utcnow()
    assert_lease(session, job.id, worker_id)
    for klass in classes:
        seen = set()
        for ref in klass.teacher_refs or []:
            teacher_id = str(ref["teacher_mb_id"])
            seen.add(teacher_id)
            _bump_from(
                counts,
                upsert_teacher_class(
                    session,
                    school_id=job.school_id,
                    teacher_mb_id=teacher_id,
                    class_mb_id=klass.mb_id,
                    show_on_reports=ref.get("show_on_reports"),
                    member_status="active",
                    job_id=job.id,
                    now=now,
                ),
            )
        existing = session.scalars(
            select(TeacherClass).where(
                TeacherClass.school_id == job.school_id,
                TeacherClass.class_mb_id == klass.mb_id,
                TeacherClass.member_status == "active",
            )
        ).all()
        for row in existing:
            if row.teacher_mb_id not in seen:
                upsert_teacher_class(
                    session,
                    school_id=job.school_id,
                    teacher_mb_id=row.teacher_mb_id,
                    class_mb_id=klass.mb_id,
                    show_on_reports=row.show_on_reports,
                    member_status="removed",
                    job_id=job.id,
                    now=now,
                )
                counts.archived += 1
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _date_span(job) -> list[date]:
    try:
        start = date.fromisoformat(job.scope["date_from"])
        end = date.fromisoformat(job.scope["date_to"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("attendance jobs require date_from and date_to") from exc
    if end < start:
        raise ContractError("attendance date_to is before date_from")
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
        if len(days) > 370:
            raise ContractError("attendance range exceeds 370 days")
    return days


def _attendance_class(session, client, job, worker_id: str) -> Counts:
    return _attendance_by_owner(
        session,
        client,
        job,
        worker_id,
        owners=session.scalars(select(ClassGroup.mb_id).where(ClassGroup.school_id == job.school_id)).all(),
        path_for=lambda owner, day: f"classes/{owner}/attendance/date/{day.isoformat()}",
        parse=lambda payload, owner, day: parse_class_attendance(payload, class_mb_id=str(owner), business_date=day),
        dataset="attendance_class",
    )


def _attendance_homeroom(session, client, job, worker_id: str) -> Counts:
    return _attendance_by_owner(
        session,
        client,
        job,
        worker_id,
        owners=session.scalars(select(YearGroup.mb_id).where(YearGroup.school_id == job.school_id)).all(),
        path_for=lambda owner, day: f"year-groups/{owner}/homeroom/attendance/date/{day.isoformat()}",
        parse=lambda payload, owner, day: parse_homeroom_attendance(payload, year_group_mb_id=str(owner), business_date=day),
        dataset="attendance_homeroom",
    )


def _attendance_by_owner(session, client, job, worker_id: str, *, owners, path_for, parse, dataset: str) -> Counts:
    days = _date_span(job)
    scope_key = f"{job.scope.get('date_from')}:{job.scope.get('date_to')}"
    checkpoint = load_checkpoint(session, school_id=job.school_id, dataset=dataset, scope_key=scope_key)
    resume = date.fromisoformat(checkpoint["next_date"]) if checkpoint.get("next_date") else days[0]
    counts = Counts()
    settings = get_settings()
    for day in days:
        if day < resume:
            continue
        drafts = []
        for owner in owners:
            page = client.get_page(path_for(owner, day))
            _store_pages(session, job, [page])
            extend_lease(session, job.id, worker_id, settings.worker_lease_seconds)
            drafts.extend(parse(page.payload, owner, day))
        assert_lease(session, job.id, worker_id)
        for draft in drafts:
            _bump_from(counts, apply_attendance(session, draft, school_id=job.school_id, job_id=job.id, now=utcnow()))
            counts.fetched += 1
        mark_coverage(session, school_id=job.school_id, dataset=dataset, window_date=day, complete=True, job_id=job.id)
        next_day = day + timedelta(days=1)
        save_checkpoint(session, school_id=job.school_id, dataset=dataset, scope_key=scope_key, cursor={"next_date": next_day.isoformat()})
        job.counts = counts.as_dict()
        session.commit()
    _finish(session, job, counts, succeeded=True, coverage_from=days[0], coverage_to=days[-1])
    session.commit()
    return counts


def _connection_check(session, client, job, worker_id: str) -> Counts:
    started = utcnow()
    try:
        page = client.get_page("school/academic-years")
    except ManageBacError as exc:
        session.add(
            ConnectionCheck(
                school_id=job.school_id,
                checked_at=utcnow(),
                ok=False,
                error_code=getattr(exc, "code", "auth"),
                latency_ms=None,
                detail="ManageBac rejected the connection check",
            )
        )
        session.commit()
        raise
    latency = int((utcnow() - started).total_seconds() * 1000)
    detail = "fixture" if get_settings().mb_auth_mode == "fixture" else "ok"
    session.add(
        ConnectionCheck(
            school_id=job.school_id,
            checked_at=utcnow(),
            ok=True,
            latency_ms=latency,
            detail=detail,
        )
    )
    _store_pages(session, job, [page])
    counts = Counts(fetched=1, unchanged=1)
    _finish(session, job, counts, succeeded=True)
    session.commit()
    return counts


def _finish(session, job, counts: Counts, *, succeeded: bool, coverage_from: date | None = None, coverage_to: date | None = None) -> None:
    record_count = _record_count(session, job.dataset, job.school_id)
    write_state(
        session,
        school_id=job.school_id,
        dataset=job.dataset,
        attempt_status="succeeded" if succeeded else "failed",
        record_count=record_count,
        error_summary=None,
        succeeded=succeeded,
        coverage_from=coverage_from,
        coverage_to=coverage_to,
    )
    job.counts = counts.as_dict()


def _record_count(session: Session, dataset: str, school_id: str) -> int | None:
    model = {
        "students": Student,
        "teachers": Teacher,
        "year_groups": YearGroup,
        "classes": ClassGroup,
        "student_year_groups": StudentYearGroup,
        "student_classes": StudentClass,
        "teacher_classes": TeacherClass,
    }.get(dataset)
    if model is None:
        return None
    return session.scalar(select(func.count()).select_from(model).where(model.school_id == school_id))


__all__ = ["Counts", "LeaseLost", "run_dataset", "_DependencyWait", "AuthError", "ContractError", "RateLimited", "TransientError", "ShapeError"]
