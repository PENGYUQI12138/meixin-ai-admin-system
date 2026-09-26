"""Disposable fictional fixture for manual browser acceptance on the M4 site."""
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

from meixin_admin.tests.run import M4_SITE, assert_isolated_site

BATCH = "BROWSER-M4-REVIEW"


def guard():
    assert_isolated_site()
    if frappe.local.site != M4_SITE:
        raise RuntimeError("Browser fixture requires the independent M4 site")


def setup():
    guard()
    if frappe.db.exists("MX Session", {"demo_batch": BATCH}):
        raise RuntimeError("Browser fixture already exists; clean it up before recreating")
    frappe.set_user("Administrator")
    settings = frappe.get_single("MX Settings")
    for field in ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule"):
        settings.set(field, "不课消")
    settings.save()
    students = [frappe.get_doc({
        "doctype": "MX Student", "student_name": f"M4浏览器虚构学生{i}",
        "guardian_phone": "00000000000", "enabled": 1, "demo_batch": BATCH,
    }).insert() for i in (1, 2)]
    teacher = frappe.get_doc({
        "doctype": "MX Teacher", "teacher_name": "M4浏览器虚构教师",
        "enabled": 1, "demo_batch": BATCH,
    }).insert()
    course = frappe.get_doc({
        "doctype": "MX Course", "course_name": "M4浏览器虚构课程",
        "default_duration_minutes": 60, "enabled": 1, "demo_batch": BATCH,
    }).insert()
    room = frappe.get_doc({
        "doctype": "MX Room", "room_name": "M4浏览器虚构教室",
        "capacity": 4, "enabled": 1, "demo_batch": BATCH,
    }).insert()
    start = now_datetime().replace(second=0, microsecond=0) - timedelta(hours=3)
    session = frappe.get_doc({
        "doctype": "MX Session", "course": course.name, "teacher": teacher.name,
        "room": room.name, "start_at": start, "end_at": start + timedelta(hours=1),
        "students": [{"student": student.name} for student in students], "demo_batch": BATCH,
    }).insert().submit()
    execution = frappe.get_doc({
        "doctype": "MX Session Execution", "session": session.name,
        "attendance": [
            {"student": students[0].name, "attendance_status": "到课"},
            {"student": students[1].name, "attendance_status": "请假"},
        ],
    }).insert().submit()
    frappe.db.commit()
    return {
        "batch": BATCH, "session": session.name, "execution": execution.name,
        "actual_start": str(start + timedelta(minutes=5)),
        "actual_end": str(start + timedelta(minutes=50)),
    }


def cleanup():
    guard()
    frappe.set_user("Administrator")
    sessions = frappe.get_all("MX Session", filters={"demo_batch": BATCH}, pluck="name")
    executions = frappe.get_all("MX Session Execution", filters={"demo_batch": BATCH}, pluck="name")
    for doctype, parents in (("MX Session Attendance", executions), ("MX Session Student", sessions)):
        if parents:
            frappe.db.delete(doctype, {"parent": ["in", parents]})
    for doctype in ("MX Teacher Hour Entry", "MX Lesson Consumption Entry", "MX Session Execution",
                    "MX Session", "MX Student", "MX Teacher", "MX Course", "MX Room"):
        names = frappe.get_all(doctype, filters={"demo_batch": BATCH}, pluck="name")
        if names:
            frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
        frappe.db.delete(doctype, {"demo_batch": BATCH})
    settings = frappe.get_single("MX Settings")
    for field in ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule"):
        settings.set(field, "未配置")
    settings.save()
    frappe.db.commit()
    return {"cleaned_batch": BATCH}
