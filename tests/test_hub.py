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
    if url.host not in {"127.0.0.1", "localhost"}:
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
        classes = session.scalars(select(StudentClass).where(StudentClass.student_mb_id == "1", StudentClass.member_status == "active")).all()
        assert {row.class_mb_id for row in classes} == {"801", "802"}
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
        assert ("99", "homeroom") in {(row.teacher_mb_id, row.advisor_role) for row in advisors}
        assert session.scalar(select(func.count()).select_from(TeacherClass).where(TeacherClass.teacher_mb_id == "99")) == 0
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
