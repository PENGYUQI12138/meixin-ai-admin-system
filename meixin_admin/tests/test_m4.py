"""M4-A teacher minutes on the isolated, fictional-data site."""
import unittest
import uuid
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

from meixin_admin.teacher_hours import confirm_teaching
from meixin_admin.tests.run import assert_isolated_site


class TestM4(unittest.TestCase):
    def setUp(self):
        assert_isolated_site()
        frappe.db.rollback()
        frappe.set_user("Administrator")
        self.batch = "TEST-M4-" + uuid.uuid4().hex[:12]
        settings = frappe.get_single("MX Settings")
        for field in ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule"):
            settings.set(field, "不课消")
        settings.save()
        self.students = [self.master("MX Student", student_name=f"M4虚构学生{i}", guardian_phone="00000000000") for i in range(3)]
        self.teacher = self.master("MX Teacher", teacher_name="M4虚构教师")
        self.course = self.master("MX Course", course_name="M4虚构课程", default_duration_minutes=60)
        self.room = self.master("MX Room", room_name="M4虚构教室", capacity=6)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()

    def master(self, doctype, **fields):
        return frappe.get_doc({"doctype": doctype, "enabled": 1, "demo_batch": self.batch, **fields}).insert()

    def execution(self, statuses):
        start = now_datetime() - timedelta(hours=3)
        session = frappe.get_doc({
            "doctype": "MX Session", "course": self.course.name, "teacher": self.teacher.name,
            "room": self.room.name, "start_at": start, "end_at": start + timedelta(hours=1),
            "students": [{"student": student.name} for student in self.students[:len(statuses)]],
            "demo_batch": self.batch,
        }).insert().submit()
        execution = frappe.get_doc({
            "doctype": "MX Session Execution", "session": session.name,
            "attendance": [
                {"student": student.name, "attendance_status": status}
                for student, status in zip(self.students[:len(statuses)], statuses, strict=True)
            ],
        }).insert().submit()
        return session, execution

    def test_normal_multi_student_and_leave_count_once(self):
        session, execution = self.execution(["到课", "到课", "请假"])
        start = session.start_at + timedelta(minutes=5)
        end = start + timedelta(minutes=45)
        name = confirm_teaching(execution.name, start, end)
        entry = frappe.get_doc("MX Teacher Hour Entry", name)
        self.assertEqual(entry.effect_minutes, 45)
        self.assertEqual(entry.teacher, self.teacher.name)
        self.assertEqual(entry.execution, execution.name)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 3)
        with self.assertRaises(frappe.ValidationError):
            confirm_teaching(execution.name, start, end)
        with self.assertRaises(frappe.PermissionError):
            entry.save()

    def test_cancel_revision_and_zero_minutes(self):
        session, execution = self.execution(["请假"])
        with self.assertRaises(frappe.PermissionError):
            confirm_teaching(execution.name, taught=0)
        first = frappe.get_doc("MX Teacher Hour Entry", confirm_teaching(
            execution.name, taught=0, exception_reason="学生请假，教师未授课",
        ))
        self.assertEqual(first.effect_minutes, 0)
        execution.cancel()
        reversal = frappe.get_doc("MX Teacher Hour Entry", frappe.db.get_value(
            "MX Teacher Hour Entry", {"reversal_of": first.name}, "name",
        ))
        self.assertEqual(reversal.effect_minutes, 0)
        amended = frappe.copy_doc(execution)
        amended.amended_from = execution.name
        amended.docstatus = 0
        for row in amended.attendance:
            row.consumption_rule_result = None
        amended.insert().submit()
        second = frappe.get_doc("MX Teacher Hour Entry", confirm_teaching(
            amended.name, taught=0, exception_reason="修订确认未授课",
        ))
        self.assertEqual(second.execution, amended.name)
        self.assertEqual(frappe.db.count("MX Teacher Hour Entry", {"session": session.name}), 3)

    def test_anomaly_and_role_gate(self):
        session, execution = self.execution(["到课"])
        scheduler = frappe.get_doc({
            "doctype": "User", "email": f"m4-{uuid.uuid4().hex[:12]}@example.invalid",
            "first_name": "M4虚构排课员", "enabled": 1, "send_welcome_email": 0,
            "user_type": "System User", "roles": [{"role": "Meixin Scheduler"}],
        }).insert().name
        outsider = frappe.get_doc({
            "doctype": "User", "email": f"m4-{uuid.uuid4().hex[:12]}@example.invalid",
            "first_name": "M4虚构外部人", "enabled": 1, "send_welcome_email": 0,
            "user_type": "System User",
        }).insert().name
        frappe.set_user(outsider)
        with self.assertRaises(frappe.PermissionError):
            confirm_teaching(execution.name, session.start_at, session.end_at)
        frappe.set_user(scheduler)
        with self.assertRaises(frappe.PermissionError):
            confirm_teaching(execution.name, session.start_at - timedelta(minutes=10), session.end_at,
                             exception_reason="提前开始")
        with self.assertRaises(frappe.ValidationError):
            confirm_teaching(execution.name, taught=0)
        name = confirm_teaching(execution.name, session.start_at, session.end_at)
        self.assertEqual(frappe.db.get_value("MX Teacher Hour Entry", name, "effect_minutes"), 60)
        self.assertFalse(frappe.has_permission("MX Teacher Hour Entry", "create"))
