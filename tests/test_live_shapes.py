from datetime import date

from hub.normalize import (
    advisor_rows,
    category_name,
    derive_person_status,
    iter_subjects,
    parse_homeroom_attendance,
    standardize_attendance,
    student_ids_from_membership,
)


def test_grouped_subjects_and_flat_subjects_both_parse():
    grouped = iter_subjects({"subjects": {"diploma": [{"id": 1, "name": "English"}], "ps": [{"id": 2, "name": "Science"}]}})
    assert {(row["program_code"], row["subject"]["id"]) for row in grouped} == {("diploma", 1), ("ps", 2)}
    listed = iter_subjects({"subjects": [{"id": 3, "name": "Math", "program_code": "myp"}]})
    assert listed[0]["program_code"] == "myp"


def test_student_levels_override_ids_and_old_membership_lists_still_work():
    live = student_ids_from_membership({"student_ids": [1, 2], "student_levels": {"1": "HL", "2": "SL"}})
    assert dict(live) == {"1": "HL", "2": "SL"}
    legacy = student_ids_from_membership({"student_ids": [1], "memberships": [{"student_id": 1, "level": "SL"}]})
    assert legacy == [("1", "SL")]


def test_advisor_roles_array_and_single_role():
    rows = advisor_rows({"advisors": [{"id": 9, "roles": ["homeroom", "cas", "homeroom"]}, {"id": 8, "role": "EE"}]})
    assert rows == [("9", "homeroom"), ("9", "cas"), ("8", "EE")]


def test_homeroom_reads_nested_attendance_and_keeps_flat_payloads():
    nested = parse_homeroom_attendance(
        {"students": [{"id": 1, "attendance": {"status": "Early Leave", "note": "left", "category": {"status": 1}}}]},
        year_group_mb_id="501",
        business_date=date(2026, 11, 12),
    )
    assert nested[0].source_status == "Early Leave"
    assert nested[0].standard_status == "early_leave"
    assert nested[0].category is None
    assert nested[0].note == "left"
    flat = parse_homeroom_attendance(
        {"students": [{"id": 2, "status": "Late", "category": "In class"}]},
        year_group_mb_id="501",
        business_date=date(2026, 11, 12),
    )
    assert flat[0].standard_status == "late"
    assert flat[0].category == "In class"


def test_exit_dates_set_status_without_replacing_an_explicit_status():
    assert derive_person_status(None, True, withdrawn_on="2025-02-18") == "withdrawn"
    assert derive_person_status(None, False, graduated_on=date(2026, 7, 15)) == "graduated"
    assert derive_person_status("enrolled", False, withdrawn_on="2025-02-18") == "enrolled"
    assert derive_person_status(None, False) == "active"
    assert standardize_attendance("Sick Leave(Excused)") == "excused"
    assert standardize_attendance("Left-early") == "unknown"
    assert category_name({"status": 1}) is None
    assert category_name("In class") == "In class"
