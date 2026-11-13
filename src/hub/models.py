from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator):
    """Store UTC in MySQL DATETIME. RDS MySQL has no timestamptz."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and getattr(value, "tzinfo", None) is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


def _uuid() -> Mapped[object]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class ApiResponse(Base):
    __tablename__ = "api_responses"
    __table_args__ = {"schema": "hub_raw"}

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "mb_id", name="uq_students_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str | None] = mapped_column(String(128))
    identifier: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(200))
    last_name: Mapped[str | None] = mapped_column(String(200))
    other_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    program: Mapped[str | None] = mapped_column(String(120))
    program_code: Mapped[str | None] = mapped_column(String(64))
    grade: Mapped[str | None] = mapped_column(String(64))
    year_group_mb_id: Mapped[str | None] = mapped_column(String(64))
    homeroom_advisor_mb_id: Mapped[str | None] = mapped_column(String(64))
    graduating_year: Mapped[int | None] = mapped_column(Integer)
    source_status: Mapped[str | None] = mapped_column(String(64))
    standard_status: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[bool | None] = mapped_column(Boolean)
    withdrawn_on: Mapped[date | None] = mapped_column(Date)
    graduated_on: Mapped[date | None] = mapped_column(Date)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    absent_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class Teacher(Base):
    __tablename__ = "teachers"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "mb_id", name="uq_teachers_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(200))
    last_name: Mapped[str | None] = mapped_column(String(200))
    other_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    source_status: Mapped[str | None] = mapped_column(String(64))
    standard_status: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[bool | None] = mapped_column(Boolean)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    absent_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class AcademicYear(Base):
    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "program_code", "mb_id", name="uq_years_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    program_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool | None] = mapped_column(Boolean)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class Term(Base):
    __tablename__ = "terms"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "mb_id", name="uq_terms_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    academic_year_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    program_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool | None] = mapped_column(Boolean)
    exam_grade: Mapped[bool | None] = mapped_column(Boolean)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "program_code", "mb_id", name="uq_subjects_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    program_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class Course(Base):
    """Reserved. Public v2 has no independent Course resource, so sync does not write here."""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "mb_id", name="uq_courses_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class YearGroup(Base):
    __tablename__ = "year_groups"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "mb_id", name="uq_year_groups_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    program: Mapped[str | None] = mapped_column(String(120))
    program_code: Mapped[str | None] = mapped_column(String(64))
    grade: Mapped[str | None] = mapped_column(String(64))
    archived: Mapped[bool | None] = mapped_column(Boolean)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class ClassGroup(Base):
    __tablename__ = "classes"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "mb_id", name="uq_classes_source"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300))
    uniq_id: Mapped[str | None] = mapped_column(String(128))
    program: Mapped[str | None] = mapped_column(String(120))
    program_code: Mapped[str | None] = mapped_column(String(64))
    grade: Mapped[str | None] = mapped_column(String(64))
    subject_mb_id: Mapped[str | None] = mapped_column(String(64))
    subject_name: Mapped[str | None] = mapped_column(String(200))
    start_term_mb_id: Mapped[str | None] = mapped_column(String(64))
    end_term_mb_id: Mapped[str | None] = mapped_column(String(64))
    archived: Mapped[bool | None] = mapped_column(Boolean)
    # None means the class payload did not include a teacher list. [] means it included an empty list.
    teacher_refs: Mapped[list | None] = mapped_column(JSON)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class StudentYearGroup(Base):
    __tablename__ = "student_year_groups"
    __table_args__ = (
        UniqueConstraint("school_id", "student_mb_id", "year_group_mb_id", name="uq_student_year_group"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    year_group_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    member_status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class StudentClass(Base):
    __tablename__ = "student_classes"
    __table_args__ = (
        UniqueConstraint("school_id", "student_mb_id", "class_mb_id", name="uq_student_class"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    class_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str | None] = mapped_column(String(16))
    member_status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class TeacherClass(Base):
    __tablename__ = "teacher_classes"
    __table_args__ = (
        UniqueConstraint("school_id", "teacher_mb_id", "class_mb_id", name="uq_teacher_class"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    class_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    show_on_reports: Mapped[bool | None] = mapped_column(Boolean)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="class_teacher")
    member_status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class YearGroupAdvisor(Base):
    """Advisory roles stay here. They are not copied into teacher_classes."""

    __tablename__ = "year_group_advisors"
    __table_args__ = (
        UniqueConstraint(
            "school_id", "year_group_mb_id", "teacher_mb_id", "advisor_role", name="uq_year_group_advisor"
        ),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    year_group_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    teacher_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    advisor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class RelationshipObservation(Base):
    __tablename__ = "relationship_observations"
    __table_args__ = (
        Index("ix_relationship_observations_open", "school_id", "relation_type", "observed_to"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    student_mb_id: Mapped[str | None] = mapped_column(String(64))
    teacher_mb_id: Mapped[str | None] = mapped_column(String(64))
    class_mb_id: Mapped[str | None] = mapped_column(String(64))
    year_group_mb_id: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(64))
    member_status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    observed_to: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        Index("ix_attendance_date", "school_id", "business_date", "attendance_type"),
        UniqueConstraint(
            "school_id",
            "attendance_type",
            "student_mb_id",
            "business_date",
            "class_key",
            "year_group_key",
            "period",
            name="uq_attendance_natural",
        ),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attendance_type: Mapped[str] = mapped_column(String(16), nullable=False)
    student_mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    class_mb_id: Mapped[str | None] = mapped_column(String(64))
    year_group_mb_id: Mapped[str | None] = mapped_column(String(64))
    # MySQL unique indexes treat NULL as distinct, so the natural key uses empty string for a missing side.
    class_key: Mapped[str] = mapped_column(String(64), Computed("IFNULL(class_mb_id, '')", persisted=True))
    year_group_key: Mapped[str] = mapped_column(String(64), Computed("IFNULL(year_group_mb_id, '')", persisted=True))
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    source_status: Mapped[str | None] = mapped_column(String(64))
    standard_status: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    source_record_id: Mapped[str | None] = mapped_column(String(64))
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class BehaviorNote(Base):
    """Teacher-submitted behavior notes from GET /behavior/notes."""

    __tablename__ = "behavior_notes"
    __table_args__ = (
        UniqueConstraint("school_id", "source_system", "mb_id", name="uq_behavior_notes_source"),
        Index("ix_behavior_notes_student", "school_id", "student_mb_id"),
        {"schema": "hub_core"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="managebac")
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_mb_id: Mapped[str | None] = mapped_column(String(64))
    student_id: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(320))
    first_name: Mapped[str | None] = mapped_column(String(200))
    last_name: Mapped[str | None] = mapped_column(String(200))
    grade: Mapped[str | None] = mapped_column(String(64))
    incident_time: Mapped[datetime | None] = mapped_column(UTCDateTime())
    behavior_type: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    next_step: Mapped[str | None] = mapped_column(Text)
    next_step_date: Mapped[date | None] = mapped_column(Date)
    reported_by: Mapped[str | None] = mapped_column(String(200))
    homeroom_advisor: Mapped[str | None] = mapped_column(String(200))
    author_id: Mapped[str | None] = mapped_column(String(64))
    visible_to_parents: Mapped[bool | None] = mapped_column(Boolean)
    visible_to_students: Mapped[bool | None] = mapped_column(Boolean)
    source_created_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    normalized_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class AttendanceRevision(Base):
    __tablename__ = "attendance_revisions"
    __table_args__ = {"schema": "hub_core"}

    id: Mapped[object] = _uuid()
    attendance_record_id: Mapped[object] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hub_core.attendance_records.id"), nullable=False
    )
    previous_status: Mapped[str | None] = mapped_column(String(64))
    new_status: Mapped[str | None] = mapped_column(String(64))
    previous_category: Mapped[str | None] = mapped_column(String(120))
    new_category: Mapped[str | None] = mapped_column(String(120))
    revised_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sync_job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)


class SyncSchedule(Base):
    __tablename__ = "sync_schedules"
    __table_args__ = {"schema": "hub_ops"}

    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    hour: Mapped[int | None] = mapped_column(Integer)
    minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        UniqueConstraint("school_id", "idempotency_key", name="uq_sync_job_idempotency"),
        CheckConstraint(
            "status IN ('queued','running','retry_wait','succeeded','partial_failed','failed')",
            name="ck_sync_job_status",
        ),
        UniqueConstraint("running_scope", name="uq_sync_jobs_one_running"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(200))
    scope: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL for every status except running, so MySQL can keep a unique lock without partial indexes.
    running_scope: Mapped[str | None] = mapped_column(
        String(160),
        Computed("CASE WHEN status = 'running' THEN CONCAT(school_id, ':', dataset) ELSE NULL END", persisted=True),
    )
    run_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_by: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SyncAttempt(Base):
    __tablename__ = "sync_attempts"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), ForeignKey("hub_ops.sync_jobs.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"
    __table_args__ = (
        UniqueConstraint("school_id", "dataset", "scope_key", name="uq_checkpoint"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False)
    cursor: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DatasetSyncState(Base):
    __tablename__ = "dataset_sync_state"
    __table_args__ = (
        UniqueConstraint("school_id", "dataset", name="uq_dataset_state"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_attempt_status: Mapped[str | None] = mapped_column(String(32))
    coverage_from: Mapped[date | None] = mapped_column(Date)
    coverage_to: Mapped[date | None] = mapped_column(Date)
    record_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="never")
    error_summary: Mapped[str | None] = mapped_column(Text)


class CoverageWindow(Base):
    __tablename__ = "coverage_windows"
    __table_args__ = (
        UniqueConstraint("school_id", "dataset", "window_date", name="uq_coverage_window"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    window_date: Mapped[date] = mapped_column(Date, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    job_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ConnectionCheck(Base):
    __tablename__ = "connection_checks"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(String(300))


class QualityIssue(Base):
    __tablename__ = "quality_issues"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    mb_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ApiClient(Base):
    __tablename__ = "api_clients"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requests_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    records_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ApiCredential(Base):
    __tablename__ = "api_credentials"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    client_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), ForeignKey("hub_ops.api_clients.id"), nullable=False)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ClientPermission(Base):
    __tablename__ = "client_permissions"
    __table_args__ = (
        UniqueConstraint("client_id", "scope", name="uq_client_scope"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    client_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), ForeignKey("hub_ops.api_clients.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)


class ClientFieldGrant(Base):
    """Allow-list of response fields for one read API. No rows means every default field."""

    __tablename__ = "client_field_grants"
    __table_args__ = (
        UniqueConstraint("client_id", "resource", "field_name", name="uq_client_field"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    client_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), ForeignKey("hub_ops.api_clients.id"), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)


class ClientRange(Base):
    __tablename__ = "client_ranges"
    __table_args__ = (
        UniqueConstraint("client_id", "dimension", "mb_id", name="uq_client_range"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    client_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), ForeignKey("hub_ops.api_clients.id"), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    mb_id: Mapped[str] = mapped_column(String(64), nullable=False)


class ApiRequestLog(Base):
    __tablename__ = "api_request_logs"
    __table_args__ = (
        Index("ix_api_request_logs_client_time", "client_id", "requested_at"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    credential_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    http_method: Mapped[str] = mapped_column(String(16), nullable=False)
    route_template: Mapped[str] = mapped_column(String(200), nullable=False)
    filter_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    returned_record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    response_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    authenticated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ApiUsageBucket(Base):
    __tablename__ = "api_usage_buckets"
    __table_args__ = (
        UniqueConstraint("client_id", "bucket_size", "bucket_start", "route_template", name="uq_usage_bucket"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    client_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bucket_size: Mapped[str] = mapped_column(String(16), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    route_template: Mapped[str] = mapped_column(String(200), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class QuotaCounter(Base):
    __tablename__ = "quota_counters"
    __table_args__ = (
        UniqueConstraint("client_id", "window_kind", "window_start", name="uq_quota_window"),
        {"schema": "hub_ops"},
    )

    id: Mapped[object] = _uuid()
    client_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), nullable=False)
    window_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    admin_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    school_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    client_id: Mapped[object | None] = mapped_column(Uuid(as_uuid=True))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AdminUser(Base):
    __tablename__ = "admins"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = {"schema": "hub_ops"}

    id: Mapped[object] = _uuid()
    admin_id: Mapped[object] = mapped_column(Uuid(as_uuid=True), ForeignKey("hub_ops.admins.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
