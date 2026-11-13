from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from hub.catalog import DATASETS
from hub.db import session_for
from hub.jobs import enqueue_job
from hub.models import (
    AttendanceRecord,
    AttendanceRevision,
    BehaviorNote,
    RelationshipObservation,
    Student,
    StudentClass,
    SyncJob,
    TeacherClass,
    YearGroupAdvisor,
)
from hub.scheduler import tick
from hub.seed import drain
from hub.web.admin_routes import ensure_bootstrap_admin
from hub.web.app import create_app


def truncate() -> None:
    session = session_for("migrate")
    try:
        rows = session.execute(
            text(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema IN ('hub_raw', 'hub_core', 'hub_ops')
                  AND table_type = 'BASE TABLE'
                  AND table_name <> 'alembic_version'
                """
            )
        ).all()
        session.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for schema, name in rows:
            session.execute(text(f"TRUNCATE TABLE {schema}.{name}"))
        session.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        session.commit()
    finally:
        session.close()


@pytest.fixture(scope="module")
def synced():
    url = session_for("migrate").get_bind().url
    session_for("migrate").close()
    if url.host not in {"127.0.0.1", "localhost", "mysql"}:
        raise RuntimeError("refusing to truncate a non-local database")
    truncate()
    ensure_bootstrap_admin()
    tick()
    drain()
    return True


def test_fixture_sync_publishes_people_and_attendance(synced):
    session = session_for("sync")
    try:
        assert session.scalar(select(func.count()).select_from(Student)) == 3
        ada = session.scalars(select(Student).where(Student.mb_id == "1")).one()
        assert ada.student_id == "S001"
        assert ada.email == "ada.chan@example.edu"
        assert ada.grade == "Grade 10"
        assert ada.standard_status == "enrolled"
        withdrawn = session.scalars(select(Student).where(Student.mb_id == "2")).one()
        assert withdrawn.standard_status == "withdrawn"
        assert withdrawn.withdrawn_on == date(2025, 2, 18)
        graduated = session.scalars(select(Student).where(Student.mb_id == "3")).one()
        assert graduated.standard_status == "graduated"
        assert graduated.graduated_on == date(2026, 7, 15)
        classes = session.scalars(select(StudentClass).where(StudentClass.student_mb_id == "1", StudentClass.member_status == "active")).all()
        assert {row.class_mb_id for row in classes} == {"801", "802"}
        assert {row.class_mb_id: row.level for row in classes} == {"801": "SL", "802": "SL"}
        periods = session.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.student_mb_id == "1",
                AttendanceRecord.attendance_type == "class",
                AttendanceRecord.class_mb_id == "801",
                AttendanceRecord.business_date == date(2026, 11, 10),
            )
        ).all()
        assert {row.period for row in periods} == {"1", "2"}
        unknown = session.scalars(select(AttendanceRecord).where(AttendanceRecord.source_status == "Left-early")).one()
        assert unknown.standard_status == "unknown"
        advisors = session.scalars(select(YearGroupAdvisor)).all()
        advisor_pairs = {(row.teacher_mb_id, row.advisor_role) for row in advisors}
        assert ("99", "homeroom") in advisor_pairs
        assert {("98", "EE"), ("98", "TOK")} <= advisor_pairs
        assert session.scalar(select(func.count()).select_from(TeacherClass).where(TeacherClass.teacher_mb_id == "99")) == 0
        homeroom = session.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.attendance_type == "homeroom",
                AttendanceRecord.business_date == date(2026, 11, 10),
            )
        ).all()
        assert {(row.student_mb_id, row.source_status, row.standard_status, row.category, row.note) for row in homeroom} == {
            ("1", "Present", "present", None, ""),
            ("2", "Early Leave", "early_leave", None, "left"),
            ("3", "Sick Leave(Excused)", "excused", None, ""),
        }
        class_notes = session.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.attendance_type == "class",
                AttendanceRecord.business_date == date(2026, 11, 10),
            )
        ).all()
        assert { (row.student_mb_id, row.class_mb_id, row.period, row.note) for row in class_notes } >= {
            ("1", "801", "1", "on time"),
            ("1", "801", "2", "bus"),
            ("3", "802", "1", "clinic"),
        }
        notes = session.scalars(select(BehaviorNote).order_by(BehaviorNote.mb_id)).all()
        assert [(row.mb_id, row.student_mb_id, row.notes) for row in notes] == [
            ("9001", "1", "Helped a classmate during lab."),
            ("9002", "1", "Arrived after the second bell."),
        ]
    finally:
        session.close()
    analytics = session_for("analytics")
    try:
        roster = analytics.scalar(text("SELECT COUNT(*) FROM hub_analytics.v_student_roster"))
        assert roster == 3
    finally:
        analytics.close()


def test_second_sync_does_not_duplicate_history(synced):
    session = session_for("sync")
    try:
        before = session.scalar(select(func.count()).select_from(RelationshipObservation))
        job = enqueue_job(session, dataset="student_classes", trigger="manual", idempotency_key="again-student-classes", scope={"mode": "full"})
        session.commit()
        job_id = job.id
    finally:
        session.close()
    drain()
    session = session_for("sync")
    try:
        assert session.scalar(select(func.count()).select_from(RelationshipObservation)) == before
        assert session.get(SyncJob, job_id).status == "succeeded"
    finally:
        session.close()


def test_attendance_revision_does_not_add_a_second_fact(synced):
    from hub.connector import ManageBacClient
    from hub.fixtures import resolve_fixture
    from hub.jobs import enqueue_job as enqueue
    from hub.worker import run_once

    def resolver(path, params):
        import json

        if path.endswith("/classes/801/attendance/date/2026-11-10"):
            return 200, json.dumps(
                {
                    "students": [
                        {"id": 1, "attendance": {"1": {"status": "Absent", "category": "Unexcused"}, "2": {"status": "Late", "category": "In class"}}}
                    ]
                }
            )
        return 200, json.dumps(resolve_fixture(path, params))

    session = session_for("sync")
    try:
        enqueue(
            session,
            dataset="attendance_class",
            trigger="manual",
            idempotency_key="attendance-revision",
            scope={"mode": "full", "date_from": "2026-11-10", "date_to": "2026-11-10"},
        )
        session.commit()
    finally:
        session.close()
    settings = __import__("hub.config", fromlist=["get_settings"]).get_settings()
    client = ManageBacClient(settings, resolver=resolver)
    assert run_once("revision-worker", client)
    session = session_for("sync")
    try:
        rows = session.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.student_mb_id == "1",
                AttendanceRecord.class_mb_id == "801",
                AttendanceRecord.business_date == date(2026, 11, 10),
                AttendanceRecord.period == "1",
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].source_status == "Absent"
        assert session.scalar(select(func.count()).select_from(AttendanceRevision).where(AttendanceRevision.attendance_record_id == rows[0].id)) == 1
    finally:
        session.close()


def test_courses_stay_blocked_and_api_hides_email_without_contact_scope(synced):
    assert DATASETS["courses"].blocked
    app = create_app()
    client = TestClient(app)
    login = client.post("/admin/api/login", json={"email": "admin@example.com", "password": "change-me"})
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    created = client.post(
        "/admin/api/clients",
        json={
            "name": "portal",
            "scopes": ["students:read", "attendance:read", "academics:read"],
            "requests_per_minute": 10,
            "records_per_day": 20,
            "bytes_per_day": 100000,
        },
        headers={"x-csrf-token": csrf},
    )
    assert created.status_code == 200, created.text
    secret = created.json()["credential"]["secret"]
    listed = client.get("/api/v1/students", headers={"Authorization": f"Bearer {secret}"})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["meta"]["returned_count"] == 3
    assert "email" not in body["data"][0]
    blocked = client.get("/api/v1/courses", headers={"Authorization": f"Bearer {secret}"})
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "DATASET_BLOCKED"
    roster = client.get("/admin/api/analytics/summary")
    assert roster.status_code == 200
    assert roster.json()["student_count"] == 3
    assert roster.json()["attendance_rate_published"] is False


def test_attendance_and_behavior_notes_are_served(synced):
    app = create_app()
    client = TestClient(app)
    login = client.post("/admin/api/login", json={"email": "admin@example.com", "password": "change-me"})
    csrf = login.json()["csrf_token"]
    created = client.post(
        "/admin/api/clients",
        json={
            "name": "notes-reader",
            "scopes": ["attendance:read", "behavior:read"],
            "requests_per_minute": 30,
            "records_per_day": 100,
            "bytes_per_day": 100000,
        },
        headers={"x-csrf-token": csrf},
    )
    assert created.status_code == 200, created.text
    secret = created.json()["credential"]["secret"]
    headers = {"Authorization": f"Bearer {secret}"}
    attendance = client.get("/api/v1/attendance?date_from=2026-11-10&date_to=2026-11-10&attendance_type=homeroom", headers=headers)
    assert attendance.status_code == 200, attendance.text
    assert all("note" in item for item in attendance.json()["data"])
    homeroom = {(item["student_mb_id"], item["note"]) for item in attendance.json()["data"]}
    assert ("2", "left") in homeroom
    class_day = client.get("/api/v1/attendance?date_from=2026-11-10&date_to=2026-11-10&attendance_type=class", headers=headers)
    assert class_day.status_code == 200, class_day.text
    assert any(item["note"] == "clinic" for item in class_day.json()["data"])
    behavior = client.get("/api/v1/behavior-notes", headers=headers)
    assert behavior.status_code == 200, behavior.text
    body = behavior.json()
    assert body["meta"]["returned_count"] == 2
    assert {item["notes"] for item in body["data"]} == {
        "Helped a classmate during lab.",
        "Arrived after the second bell.",
    }
    assert "email" not in body["data"][0]


def test_user_can_open_live_reports_only(synced):
    app = create_app()
    admin = TestClient(app)
    login = admin.post("/admin/api/login", json={"email": "admin@example.com", "password": "change-me"})
    csrf = login.json()["csrf_token"]
    created = admin.post(
        "/admin/api/users",
        json={"email": "viewer@example.com", "password": "change-me-2", "role": "user"},
        headers={"x-csrf-token": csrf},
    )
    assert created.status_code == 200, created.text
    assert created.json()["user"]["role"] == "user"
    viewer = TestClient(app)
    signed = viewer.post("/admin/api/login", json={"email": "viewer@example.com", "password": "change-me-2"})
    assert signed.status_code == 200, signed.text
    assert signed.json()["role"] == "user"
    assert viewer.get("/admin/api/overview").status_code == 403
    assert viewer.get("/admin/api/clients").status_code == 403
    reports = viewer.get("/admin/api/analytics/reports")
    assert reports.status_code == 200, reports.text
    assert {item["key"] for item in reports.json()["reports"]} == {"student_attendance", "teacher_assignments"}
    attendance = viewer.get("/admin/api/analytics/reports/student_attendance?date_from=2026-11-10&date_to=2026-11-10&student=Bob")
    assert attendance.status_code == 200, attendance.text
    assert "left" in {row["note"] for row in attendance.json()["data"]}
    teaching = viewer.get("/admin/api/analytics/reports/teacher_assignments?subject=English")
    assert teaching.status_code == 200, teaching.text
    assert any(row["teacher"] == "Ann Lee" and row["subject"] == "English" and row["course"] == "English A" for row in teaching.json()["data"])


def test_field_grants_hide_unselected_fields_and_usage_is_counted(synced):
    app = create_app()
    admin = TestClient(app)
    login = admin.post("/admin/api/login", json={"email": "admin@example.com", "password": "change-me"})
    csrf = login.json()["csrf_token"]
    created = admin.post(
        "/admin/api/clients",
        json={
            "name": "status-only",
            "scopes": ["attendance:read"],
            "fields": {"attendance": ["student_mb_id", "status"]},
            "requests_per_minute": 30,
            "records_per_day": 100,
            "bytes_per_day": 100000,
        },
        headers={"x-csrf-token": csrf},
    )
    assert created.status_code == 200, created.text
    secret = created.json()["credential"]["secret"]
    attendance = admin.get(
        "/api/v1/attendance?date_from=2026-11-10&date_to=2026-11-10",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert attendance.status_code == 200, attendance.text
    assert attendance.json()["data"]
    assert all(set(item) == {"student_mb_id", "status"} for item in attendance.json()["data"])
    usage = admin.get("/admin/api/usage")
    assert usage.status_code == 200, usage.text
    routes = {row["route"]: row["request_count"] for row in usage.json()["by_route"]}
    assert routes.get("/api/v1/attendance", 0) >= 1
    removed = admin.delete(f"/admin/api/clients/{created.json()['client']['id']}", headers={"x-csrf-token": csrf})
    assert removed.status_code == 200, removed.text


def test_admin_can_set_dataset_sync_frequency(synced):
    app = create_app()
    admin = TestClient(app)
    login = admin.post("/admin/api/login", json={"email": "admin@example.com", "password": "change-me"})
    csrf = login.json()["csrf_token"]
    updated = admin.patch(
        "/admin/api/schedules/behavior_notes",
        json={"cadence": "hourly", "minute": 20, "enabled": True},
        headers={"x-csrf-token": csrf},
    )
    assert updated.status_code == 200, updated.text
    listed = admin.get("/admin/api/schedules")
    row = next(item for item in listed.json()["data"] if item["dataset"] == "behavior_notes")
    assert row["cadence"] == "hourly"
    assert row["minute"] == 20
    assert row["enabled"] is True


def test_cursor_cannot_be_reused_for_a_different_filter(synced):
    app = create_app()
    client = TestClient(app)
    login = client.post("/admin/api/login", json={"email": "admin@example.com", "password": "change-me"})
    csrf = login.json()["csrf_token"]
    created = client.post(
        "/admin/api/clients",
        json={
            "name": "pager",
            "scopes": ["students:read"],
            "requests_per_minute": 30,
            "records_per_day": 100,
            "bytes_per_day": 100000,
        },
        headers={"x-csrf-token": csrf},
    )
    secret = created.json()["credential"]["secret"]
    first = client.get("/api/v1/students?limit=1", headers={"Authorization": f"Bearer {secret}"})
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor
    reused = client.get(f"/api/v1/students?limit=1&cursor={cursor}&year_group_id=999", headers={"Authorization": f"Bearer {secret}"})
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "CURSOR_INVALID"


def test_quota_blocks_the_second_wave(synced):
    app = create_app()
    client = TestClient(app)
    login = client.post("/admin/api/login", json={"email": "admin@example.com", "password": "change-me"})
    csrf = login.json()["csrf_token"]
    created = client.post(
        "/admin/api/clients",
        json={
            "name": "limited",
            "scopes": ["students:read"],
            "requests_per_minute": 10,
            "records_per_day": 3,
            "bytes_per_day": 100000,
        },
        headers={"x-csrf-token": csrf},
    )
    secret = created.json()["credential"]["secret"]
    headers = {"Authorization": f"Bearer {secret}"}
    assert client.get("/api/v1/students", headers=headers).status_code == 200
    blocked = client.get("/api/v1/students", headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "QUOTA_EXCEEDED"
