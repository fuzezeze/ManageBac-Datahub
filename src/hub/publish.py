import hashlib
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.connector import Page, body_sha256
from hub.models import (
    AcademicYear,
    ApiResponse,
    AttendanceRecord,
    AttendanceRevision,
    BehaviorNote,
    ClassGroup,
    CoverageWindow,
    DatasetSyncState,
    QualityIssue,
    RelationshipObservation,
    Student,
    StudentClass,
    StudentYearGroup,
    Subject,
    SyncCheckpoint,
    Teacher,
    TeacherClass,
    Term,
    YearGroup,
    YearGroupAdvisor,
)
from hub.normalize import (
    AttendanceDraft,
    BehaviorNoteDraft,
    derive_person_status,
    parse_date,
    parse_timestamp,
    present_bool,
    present_int,
    present_string,
)
from hub.timeutil import utcnow


class LeaseLost(Exception):
    pass


def save_raw(session: Session, *, school_id: str, job_id: UUID, page: Page) -> None:
    session.add(
        ApiResponse(
            school_id=school_id,
            job_id=job_id,
            endpoint=page.endpoint,
            params=page.params,
            http_status=page.status,
            fetched_at=utcnow(),
            body=page.body_text,
            sha256=body_sha256(page.body_text),
            record_count=page.record_count,
        )
    )
    session.commit()


def assert_lease(session: Session, job_id: UUID, worker_id: str) -> None:
    from hub.models import SyncJob

    job = session.get(SyncJob, job_id)
    if job is None or job.locked_by != worker_id or job.lease_expires_at is None or job.lease_expires_at <= utcnow():
        raise LeaseLost()


def extend_lease(session: Session, job_id: UUID, worker_id: str, seconds: int) -> None:
    from hub.models import SyncJob

    job = session.get(SyncJob, job_id)
    if job is None or job.locked_by != worker_id:
        raise LeaseLost()
    now = utcnow()
    job.heartbeat_at = now
    job.lease_expires_at = now + _seconds(seconds)
    session.commit()


def _seconds(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def add_quality(session: Session, *, school_id: str, job_id: UUID, dataset: str, code: str, message: str, mb_id: str | None = None) -> None:
    session.add(
        QualityIssue(
            school_id=school_id,
            job_id=job_id,
            dataset=dataset,
            code=code,
            message=message[:500],
            mb_id=mb_id,
            created_at=utcnow(),
        )
    )


def _touch_seen(row, now: datetime, job_id: UUID, changed: bool) -> str:
    row.last_seen_at = now
    row.sync_job_id = job_id
    if changed:
        row.normalized_updated_at = now
        return "updated"
    return "unchanged"


def upsert_student(session: Session, payload: dict, *, school_id: str, job_id: UUID, now: datetime) -> str:
    mb_id = str(payload["id"])
    row = session.scalars(
        select(Student).where(Student.school_id == school_id, Student.mb_id == mb_id)
    ).one_or_none()
    created = row is None
    if row is None:
        row = Student(
            school_id=school_id,
            mb_id=mb_id,
            standard_status="unspecified",
            ingested_at=now,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    before = _person_snapshot(row)
    row.student_id = present_string(payload, "student_id", row.student_id)
    row.identifier = present_string(payload, "identifier", row.identifier)
    row.first_name = present_string(payload, "first_name", row.first_name)
    row.last_name = present_string(payload, "last_name", row.last_name)
    row.other_name = present_string(payload, "other_name", row.other_name)
    row.email = present_string(payload, "email", row.email)
    row.program = present_string(payload, "program", row.program)
    row.program_code = present_string(payload, "program_code", row.program_code)
    row.grade = _student_grade(payload, row.grade)
    if "year_group_id" in payload:
        row.year_group_mb_id = None if payload["year_group_id"] is None else str(payload["year_group_id"])
    if "homeroom_advisor_id" in payload:
        row.homeroom_advisor_mb_id = None if payload["homeroom_advisor_id"] is None else str(payload["homeroom_advisor_id"])
    row.graduating_year = present_int(payload, "graduating_year", row.graduating_year)
    if "status" in payload:
        row.source_status = None if payload["status"] is None else str(payload["status"])
    row.archived = present_bool(payload, "archived", row.archived)
    if "withdrawn_on" in payload:
        row.withdrawn_on = parse_date(payload.get("withdrawn_on"))
    if "graduated_on" in payload:
        row.graduated_on = parse_date(payload.get("graduated_on"))
    if any(key in payload for key in ("status", "archived", "withdrawn_on", "graduated_on")) or created:
        row.standard_status = derive_person_status(
            row.source_status,
            row.archived,
            withdrawn_on=row.withdrawn_on,
            graduated_on=row.graduated_on,
        )
    if "updated_at" in payload:
        row.source_updated_at = parse_timestamp(payload.get("updated_at"))
    row.absent_since = None
    changed = created or before != _person_snapshot(row)
    if created:
        return "created"
    return _touch_seen(row, now, job_id, changed)


def upsert_teacher(session: Session, payload: dict, *, school_id: str, job_id: UUID, now: datetime) -> str:
    mb_id = str(payload["id"])
    row = session.scalars(select(Teacher).where(Teacher.school_id == school_id, Teacher.mb_id == mb_id)).one_or_none()
    created = row is None
    if row is None:
        row = Teacher(
            school_id=school_id,
            mb_id=mb_id,
            standard_status="unspecified",
            ingested_at=now,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    before = (row.identifier, row.first_name, row.last_name, row.other_name, row.email, row.source_status, row.archived, row.standard_status)
    row.identifier = present_string(payload, "identifier", row.identifier)
    row.first_name = present_string(payload, "first_name", row.first_name)
    row.last_name = present_string(payload, "last_name", row.last_name)
    row.other_name = present_string(payload, "other_name", row.other_name)
    row.email = present_string(payload, "email", row.email)
    if "status" in payload:
        row.source_status = None if payload["status"] is None else str(payload["status"])
    row.archived = present_bool(payload, "archived", row.archived)
    if "status" in payload or "archived" in payload or created:
        row.standard_status = derive_person_status(row.source_status, row.archived)
    if "updated_at" in payload:
        row.source_updated_at = parse_timestamp(payload.get("updated_at"))
    row.absent_since = None
    changed = created or before != (row.identifier, row.first_name, row.last_name, row.other_name, row.email, row.source_status, row.archived, row.standard_status)
    if created:
        return "created"
    return _touch_seen(row, now, job_id, changed)


def _person_snapshot(row: Student) -> tuple:
    return (
        row.student_id,
        row.identifier,
        row.first_name,
        row.last_name,
        row.other_name,
        row.email,
        row.program,
        row.program_code,
        row.grade,
        row.year_group_mb_id,
        row.homeroom_advisor_mb_id,
        row.graduating_year,
        row.source_status,
        row.standard_status,
        row.archived,
        row.withdrawn_on,
        row.graduated_on,
        row.source_updated_at,
    )


def _student_grade(payload: dict, current: str | None) -> str | None:
    """Live student records send class_grade. An explicit grade still wins."""
    if "grade" in payload:
        return present_string(payload, "grade", current)
    if "class_grade" in payload:
        return present_string(payload, "class_grade", current)
    return current


def mark_people_missing(session: Session, model, *, school_id: str, seen: set[str], job_id: UUID, dataset: str, now: datetime) -> int:
    marked = 0
    rows = session.scalars(select(model).where(model.school_id == school_id)).all()
    for row in rows:
        if row.mb_id in seen:
            continue
        if row.absent_since is None:
            row.absent_since = now
            marked += 1
            add_quality(
                session,
                school_id=school_id,
                job_id=job_id,
                dataset=dataset,
                code="missing_from_source",
                message="Record was absent from a completed full fetch. Status was left unchanged.",
                mb_id=row.mb_id,
            )
    return marked


def upsert_year(session: Session, program_code: str, payload: dict, *, school_id: str, job_id: UUID, now: datetime) -> str:
    mb_id = str(payload["id"])
    row = session.scalars(
        select(AcademicYear).where(
            AcademicYear.school_id == school_id,
            AcademicYear.program_code == program_code,
            AcademicYear.mb_id == mb_id,
        )
    ).one_or_none()
    created = row is None
    if row is None:
        row = AcademicYear(
            school_id=school_id,
            program_code=program_code,
            mb_id=mb_id,
            ingested_at=now,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    before = (row.name, row.starts_on, row.ends_on, row.active)
    row.name = present_string(payload, "name", row.name)
    if "starts_on" in payload:
        from hub.normalize import parse_date

        row.starts_on = parse_date(payload.get("starts_on"))
    if "ends_on" in payload:
        from hub.normalize import parse_date

        row.ends_on = parse_date(payload.get("ends_on"))
    row.active = present_bool(payload, "active", row.active)
    if "updated_at" in payload:
        row.source_updated_at = parse_timestamp(payload.get("updated_at"))
    if created:
        return "created"
    return _touch_seen(row, now, job_id, before != (row.name, row.starts_on, row.ends_on, row.active))


def upsert_term(session: Session, program_code: str, year_mb_id: str, payload: dict, *, school_id: str, job_id: UUID, now: datetime) -> str:
    from hub.normalize import parse_date

    mb_id = str(payload["id"])
    row = session.scalars(select(Term).where(Term.school_id == school_id, Term.mb_id == mb_id)).one_or_none()
    created = row is None
    if row is None:
        row = Term(
            school_id=school_id,
            mb_id=mb_id,
            academic_year_mb_id=year_mb_id,
            program_code=program_code,
            ingested_at=now,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    before = (row.name, row.starts_on, row.ends_on, row.active, row.exam_grade, row.academic_year_mb_id)
    row.academic_year_mb_id = year_mb_id
    row.program_code = program_code
    row.name = present_string(payload, "name", row.name)
    if "starts_on" in payload:
        row.starts_on = parse_date(payload.get("starts_on"))
    if "ends_on" in payload:
        row.ends_on = parse_date(payload.get("ends_on"))
    row.active = present_bool(payload, "active", row.active)
    row.exam_grade = present_bool(payload, "exam_grade", row.exam_grade)
    if "updated_at" in payload:
        row.source_updated_at = parse_timestamp(payload.get("updated_at"))
    if created:
        return "created"
    return _touch_seen(row, now, job_id, before != (row.name, row.starts_on, row.ends_on, row.active, row.exam_grade, row.academic_year_mb_id))


def upsert_subject(session: Session, program_code: str, payload: dict, *, school_id: str, job_id: UUID, now: datetime) -> str:
    mb_id = str(payload["id"])
    row = session.scalars(
        select(Subject).where(Subject.school_id == school_id, Subject.program_code == program_code, Subject.mb_id == mb_id)
    ).one_or_none()
    created = row is None
    if row is None:
        row = Subject(
            school_id=school_id,
            program_code=program_code,
            mb_id=mb_id,
            ingested_at=now,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    before = (row.name, row.code)
    row.name = present_string(payload, "name", row.name)
    row.code = present_string(payload, "code", row.code)
    if "updated_at" in payload:
        row.source_updated_at = parse_timestamp(payload.get("updated_at"))
    if created:
        return "created"
    return _touch_seen(row, now, job_id, before != (row.name, row.code))


def upsert_year_group(session: Session, payload: dict, *, school_id: str, job_id: UUID, now: datetime) -> str:
    mb_id = str(payload["id"])
    row = session.scalars(select(YearGroup).where(YearGroup.school_id == school_id, YearGroup.mb_id == mb_id)).one_or_none()
    created = row is None
    if row is None:
        row = YearGroup(
            school_id=school_id,
            mb_id=mb_id,
            ingested_at=now,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    before = (row.name, row.program, row.program_code, row.grade, row.archived)
    row.name = present_string(payload, "name", row.name)
    row.program = present_string(payload, "program", row.program)
    row.program_code = present_string(payload, "program_code", row.program_code)
    row.grade = present_string(payload, "grade", row.grade)
    row.archived = present_bool(payload, "archived", row.archived)
    if "updated_at" in payload:
        row.source_updated_at = parse_timestamp(payload.get("updated_at"))
    if created:
        return "created"
    return _touch_seen(row, now, job_id, before != (row.name, row.program, row.program_code, row.grade, row.archived))


def upsert_class(session: Session, payload: dict, *, school_id: str, job_id: UUID, now: datetime, teacher_refs) -> str:
    mb_id = str(payload["id"])
    row = session.scalars(select(ClassGroup).where(ClassGroup.school_id == school_id, ClassGroup.mb_id == mb_id)).one_or_none()
    created = row is None
    if row is None:
        row = ClassGroup(
            school_id=school_id,
            mb_id=mb_id,
            ingested_at=now,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    before = (row.name, row.uniq_id, row.program, row.grade, row.subject_mb_id, row.subject_name, row.archived, row.teacher_refs)
    row.name = present_string(payload, "name", row.name)
    row.uniq_id = present_string(payload, "uniq_id", row.uniq_id)
    row.program = present_string(payload, "program", row.program)
    row.program_code = present_string(payload, "program_code", row.program_code)
    row.grade = present_string(payload, "grade", row.grade)
    if "subject_id" in payload:
        row.subject_mb_id = None if payload["subject_id"] is None else str(payload["subject_id"])
    row.subject_name = present_string(payload, "subject_name", row.subject_name)
    if "start_term_id" in payload:
        row.start_term_mb_id = None if payload["start_term_id"] is None else str(payload["start_term_id"])
    if "end_term_id" in payload:
        row.end_term_mb_id = None if payload["end_term_id"] is None else str(payload["end_term_id"])
    row.archived = present_bool(payload, "archived", row.archived)
    if teacher_refs is not None:
        row.teacher_refs = teacher_refs
    if "updated_at" in payload:
        row.source_updated_at = parse_timestamp(payload.get("updated_at"))
    if created:
        return "created"
    after = (row.name, row.uniq_id, row.program, row.grade, row.subject_mb_id, row.subject_name, row.archived, row.teacher_refs)
    return _touch_seen(row, now, job_id, before != after)


def content_hash(*parts: object) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def observe(
    session: Session,
    *,
    school_id: str,
    relation_type: str,
    student_mb_id: str | None,
    teacher_mb_id: str | None,
    class_mb_id: str | None,
    year_group_mb_id: str | None,
    role: str | None,
    member_status: str,
    digest: str,
    now: datetime,
    job_id: UUID,
) -> None:
    stmt = select(RelationshipObservation).where(
        RelationshipObservation.school_id == school_id,
        RelationshipObservation.relation_type == relation_type,
        RelationshipObservation.observed_to.is_(None),
        RelationshipObservation.student_mb_id.is_(None) if student_mb_id is None else RelationshipObservation.student_mb_id == student_mb_id,
        RelationshipObservation.teacher_mb_id.is_(None) if teacher_mb_id is None else RelationshipObservation.teacher_mb_id == teacher_mb_id,
        RelationshipObservation.class_mb_id.is_(None) if class_mb_id is None else RelationshipObservation.class_mb_id == class_mb_id,
        RelationshipObservation.year_group_mb_id.is_(None) if year_group_mb_id is None else RelationshipObservation.year_group_mb_id == year_group_mb_id,
    )
    open_row = session.scalars(stmt).one_or_none()
    if open_row is not None and open_row.content_hash == digest:
        return
    if open_row is not None:
        open_row.observed_to = now
    session.add(
        RelationshipObservation(
            school_id=school_id,
            relation_type=relation_type,
            student_mb_id=student_mb_id,
            teacher_mb_id=teacher_mb_id,
            class_mb_id=class_mb_id,
            year_group_mb_id=year_group_mb_id,
            role=role,
            member_status=member_status,
            content_hash=digest,
            observed_from=now,
            sync_job_id=job_id,
        )
    )


def upsert_student_year_group(session: Session, *, school_id: str, student_mb_id: str, year_group_mb_id: str, member_status: str, job_id: UUID, now: datetime) -> str:
    row = session.scalars(
        select(StudentYearGroup).where(
            StudentYearGroup.school_id == school_id,
            StudentYearGroup.student_mb_id == student_mb_id,
            StudentYearGroup.year_group_mb_id == year_group_mb_id,
        )
    ).one_or_none()
    created = row is None
    if row is None:
        row = StudentYearGroup(
            school_id=school_id,
            student_mb_id=student_mb_id,
            year_group_mb_id=year_group_mb_id,
            member_status=member_status,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    changed = created or row.member_status != member_status
    row.member_status = member_status
    row.last_seen_at = now
    row.sync_job_id = job_id
    if changed:
        row.normalized_updated_at = now
    observe(
        session,
        school_id=school_id,
        relation_type="student_year_group",
        student_mb_id=student_mb_id,
        teacher_mb_id=None,
        class_mb_id=None,
        year_group_mb_id=year_group_mb_id,
        role=None,
        member_status=member_status,
        digest=content_hash(member_status),
        now=now,
        job_id=job_id,
    )
    if created:
        return "created"
    return "updated" if changed else "unchanged"


def upsert_student_class(session: Session, *, school_id: str, student_mb_id: str, class_mb_id: str, level: str | None, member_status: str, job_id: UUID, now: datetime) -> str:
    row = session.scalars(
        select(StudentClass).where(
            StudentClass.school_id == school_id,
            StudentClass.student_mb_id == student_mb_id,
            StudentClass.class_mb_id == class_mb_id,
        )
    ).one_or_none()
    created = row is None
    if row is None:
        row = StudentClass(
            school_id=school_id,
            student_mb_id=student_mb_id,
            class_mb_id=class_mb_id,
            level=level,
            member_status=member_status,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    changed = created or row.member_status != member_status or row.level != level
    row.member_status = member_status
    if level is not None or created:
        row.level = level if level is not None else row.level
    row.last_seen_at = now
    row.sync_job_id = job_id
    if changed:
        row.normalized_updated_at = now
    observe(
        session,
        school_id=school_id,
        relation_type="student_class",
        student_mb_id=student_mb_id,
        teacher_mb_id=None,
        class_mb_id=class_mb_id,
        year_group_mb_id=None,
        role=row.level,
        member_status=member_status,
        digest=content_hash(member_status, row.level),
        now=now,
        job_id=job_id,
    )
    if created:
        return "created"
    return "updated" if changed else "unchanged"


def upsert_teacher_class(session: Session, *, school_id: str, teacher_mb_id: str, class_mb_id: str, show_on_reports: bool | None, member_status: str, job_id: UUID, now: datetime) -> str:
    row = session.scalars(
        select(TeacherClass).where(
            TeacherClass.school_id == school_id,
            TeacherClass.teacher_mb_id == teacher_mb_id,
            TeacherClass.class_mb_id == class_mb_id,
        )
    ).one_or_none()
    created = row is None
    if row is None:
        row = TeacherClass(
            school_id=school_id,
            teacher_mb_id=teacher_mb_id,
            class_mb_id=class_mb_id,
            show_on_reports=show_on_reports,
            role="class_teacher",
            member_status=member_status,
            last_seen_at=now,
            normalized_updated_at=now,
            sync_job_id=job_id,
        )
        session.add(row)
    changed = created or row.member_status != member_status or row.show_on_reports != show_on_reports
    row.member_status = member_status
    row.role = "class_teacher"
    if show_on_reports is not None or created:
        row.show_on_reports = show_on_reports if show_on_reports is not None else row.show_on_reports
    row.last_seen_at = now
    row.sync_job_id = job_id
    if changed:
        row.normalized_updated_at = now
    observe(
        session,
        school_id=school_id,
        relation_type="teacher_class",
        student_mb_id=None,
        teacher_mb_id=teacher_mb_id,
        class_mb_id=class_mb_id,
        year_group_mb_id=None,
        role="class_teacher",
        member_status=member_status,
        digest=content_hash(member_status, row.show_on_reports, "class_teacher"),
        now=now,
        job_id=job_id,
    )
    if created:
        return "created"
    return "updated" if changed else "unchanged"


def replace_advisors(session: Session, *, school_id: str, year_group_mb_id: str, advisors: list[tuple[str, str]], job_id: UUID, now: datetime) -> None:
    session.query(YearGroupAdvisor).filter(
        YearGroupAdvisor.school_id == school_id,
        YearGroupAdvisor.year_group_mb_id == year_group_mb_id,
    ).delete(synchronize_session=False)
    for teacher_mb_id, role in advisors:
        session.add(
            YearGroupAdvisor(
                school_id=school_id,
                year_group_mb_id=year_group_mb_id,
                teacher_mb_id=teacher_mb_id,
                advisor_role=role,
                last_seen_at=now,
                sync_job_id=job_id,
            )
        )


def _resolve_behavior_student_mb_id(session: Session, *, school_id: str, student_id: str | None) -> str | None:
    if not student_id:
        return None
    by_mb = session.scalars(select(Student).where(Student.school_id == school_id, Student.mb_id == student_id)).one_or_none()
    if by_mb is not None:
        return by_mb.mb_id
    by_school_id = session.scalars(
        select(Student).where(Student.school_id == school_id, Student.student_id == student_id)
    ).one_or_none()
    if by_school_id is not None:
        return by_school_id.mb_id
    return None


def upsert_behavior_note(session: Session, draft: BehaviorNoteDraft, *, school_id: str, job_id: UUID, now: datetime) -> str:
    student_mb_id = _resolve_behavior_student_mb_id(session, school_id=school_id, student_id=draft.student_id)
    row = session.scalars(
        select(BehaviorNote).where(
            BehaviorNote.school_id == school_id,
            BehaviorNote.source_system == "managebac",
            BehaviorNote.mb_id == draft.mb_id,
        )
    ).one_or_none()
    values = {
        "student_mb_id": student_mb_id,
        "student_id": draft.student_id,
        "email": draft.email,
        "first_name": draft.first_name,
        "last_name": draft.last_name,
        "grade": draft.grade,
        "incident_time": draft.incident_time,
        "behavior_type": draft.behavior_type,
        "notes": draft.notes,
        "next_step": draft.next_step,
        "next_step_date": draft.next_step_date,
        "reported_by": draft.reported_by,
        "homeroom_advisor": draft.homeroom_advisor,
        "author_id": draft.author_id,
        "visible_to_parents": draft.visible_to_parents,
        "visible_to_students": draft.visible_to_students,
        "source_created_at": draft.source_created_at,
        "source_updated_at": draft.source_updated_at,
        "last_seen_at": now,
        "sync_job_id": job_id,
    }
    if row is None:
        session.add(
            BehaviorNote(
                school_id=school_id,
                source_system="managebac",
                mb_id=draft.mb_id,
                ingested_at=now,
                normalized_updated_at=now,
                **values,
            )
        )
        return "created"
    same = all(getattr(row, key) == value for key, value in values.items() if key not in {"last_seen_at", "sync_job_id"})
    for key, value in values.items():
        setattr(row, key, value)
    if same:
        return "unchanged"
    row.normalized_updated_at = now
    return "updated"


def apply_attendance(session: Session, draft: AttendanceDraft, *, school_id: str, job_id: UUID, now: datetime) -> str:
    stmt = select(AttendanceRecord).where(
        AttendanceRecord.school_id == school_id,
        AttendanceRecord.attendance_type == draft.attendance_type,
        AttendanceRecord.student_mb_id == draft.student_mb_id,
        AttendanceRecord.business_date == draft.business_date,
        AttendanceRecord.period == draft.period,
    )
    stmt = stmt.where(AttendanceRecord.class_mb_id.is_(None) if draft.class_mb_id is None else AttendanceRecord.class_mb_id == draft.class_mb_id)
    stmt = stmt.where(
        AttendanceRecord.year_group_mb_id.is_(None) if draft.year_group_mb_id is None else AttendanceRecord.year_group_mb_id == draft.year_group_mb_id
    )
    row = session.scalars(stmt).one_or_none()
    if draft.standard_status == "unknown":
        add_quality(
            session,
            school_id=school_id,
            job_id=job_id,
            dataset=f"attendance_{draft.attendance_type}",
            code="unknown_status",
            message=f"Unmapped attendance status {draft.source_status!r}",
            mb_id=draft.student_mb_id,
        )
    if row is None:
        session.add(
            AttendanceRecord(
                school_id=school_id,
                attendance_type=draft.attendance_type,
                student_mb_id=draft.student_mb_id,
                business_date=draft.business_date,
                class_mb_id=draft.class_mb_id,
                year_group_mb_id=draft.year_group_mb_id,
                period=draft.period,
                source_status=draft.source_status,
                standard_status=draft.standard_status,
                category=draft.category,
                note=draft.note,
                source_record_id=draft.source_record_id,
                ingested_at=now,
                last_seen_at=now,
                normalized_updated_at=now,
                sync_job_id=job_id,
            )
        )
        return "created"
    same = row.source_status == draft.source_status and row.category == draft.category and row.note == draft.note and row.standard_status == draft.standard_status
    if same:
        row.last_seen_at = now
        row.sync_job_id = job_id
        return "unchanged"
    session.add(
        AttendanceRevision(
            attendance_record_id=row.id,
            previous_status=row.source_status,
            new_status=draft.source_status,
            previous_category=row.category,
            new_category=draft.category,
            revised_at=now,
            sync_job_id=job_id,
        )
    )
    row.source_status = draft.source_status
    row.standard_status = draft.standard_status
    row.category = draft.category
    row.note = draft.note
    row.source_record_id = draft.source_record_id or row.source_record_id
    row.last_seen_at = now
    row.normalized_updated_at = now
    row.sync_job_id = job_id
    return "updated"


def mark_coverage(session: Session, *, school_id: str, dataset: str, window_date: date, complete: bool, job_id: UUID) -> None:
    row = session.scalars(
        select(CoverageWindow).where(
            CoverageWindow.school_id == school_id,
            CoverageWindow.dataset == dataset,
            CoverageWindow.window_date == window_date,
        )
    ).one_or_none()
    if row is None:
        session.add(
            CoverageWindow(
                school_id=school_id,
                dataset=dataset,
                window_date=window_date,
                complete=complete,
                job_id=job_id,
                updated_at=utcnow(),
            )
        )
        return
    row.complete = complete
    row.job_id = job_id
    row.updated_at = utcnow()


def write_state(
    session: Session,
    *,
    school_id: str,
    dataset: str,
    attempt_status: str,
    record_count: int | None,
    error_summary: str | None,
    succeeded: bool,
    coverage_from: date | None = None,
    coverage_to: date | None = None,
) -> None:
    now = utcnow()
    row = session.scalars(
        select(DatasetSyncState).where(DatasetSyncState.school_id == school_id, DatasetSyncState.dataset == dataset)
    ).one_or_none()
    if row is None:
        row = DatasetSyncState(school_id=school_id, dataset=dataset, status="never")
        session.add(row)
    row.last_attempt_at = now
    row.last_attempt_status = attempt_status
    row.error_summary = error_summary
    if record_count is not None:
        row.record_count = record_count
    if coverage_from is not None:
        row.coverage_from = coverage_from
    if coverage_to is not None:
        row.coverage_to = coverage_to
    if succeeded:
        row.last_success_at = now
        row.status = "fresh" if attempt_status == "succeeded" else "partial"
    elif row.last_success_at is None:
        row.status = "blocked" if attempt_status == "blocked" else "failed"
    else:
        row.status = "degraded"


def save_checkpoint(session: Session, *, school_id: str, dataset: str, scope_key: str, cursor: dict) -> None:
    row = session.scalars(
        select(SyncCheckpoint).where(
            SyncCheckpoint.school_id == school_id,
            SyncCheckpoint.dataset == dataset,
            SyncCheckpoint.scope_key == scope_key,
        )
    ).one_or_none()
    if row is None:
        session.add(
            SyncCheckpoint(
                school_id=school_id,
                dataset=dataset,
                scope_key=scope_key,
                cursor=cursor,
                updated_at=utcnow(),
            )
        )
        return
    row.cursor = cursor
    row.updated_at = utcnow()


def load_checkpoint(session: Session, *, school_id: str, dataset: str, scope_key: str) -> dict:
    row = session.scalars(
        select(SyncCheckpoint).where(
            SyncCheckpoint.school_id == school_id,
            SyncCheckpoint.dataset == dataset,
            SyncCheckpoint.scope_key == scope_key,
        )
    ).one_or_none()
    return dict(row.cursor) if row is not None else {}
