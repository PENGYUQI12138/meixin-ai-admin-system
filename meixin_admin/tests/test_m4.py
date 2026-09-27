"""M4-A teacher minutes on the isolated, fictional-data site."""
import unittest
import uuid
import json
import os
import subprocess
import sys
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import get_datetime, now_datetime

from meixin_admin import entitlements
from meixin_admin.teacher_hours import confirm_teaching
from meixin_admin.tests.run import assert_isolated_site


class TestM4(unittest.TestCase):
    def setUp(self):
        assert_isolated_site()
        frappe.db.rollback()
        frappe.set_user("Administrator")
        self.batch = "TEST-M4-" + uuid.uuid4().hex[:12]
        self.committed = False
        self.workers = []
        settings = frappe.get_single("MX Settings")
        for field in ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule"):
            settings.set(field, "不课消")
        settings.save()
        self.students = [self.master("MX Student", student_name=f"M4虚构学生{i}", guardian_phone="00000000000") for i in range(3)]
        self.teacher = self.master("MX Teacher", teacher_name="M4虚构教师")
        self.course = self.master("MX Course", course_name="M4虚构课程", default_duration_minutes=60)
        self.room = self.master("MX Room", room_name="M4虚构教室", capacity=6)

    def tearDown(self):
        for worker in self.workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=10)
        frappe.set_user("Administrator")
        frappe.db.rollback()
        if self.committed:
            sessions = frappe.get_all("MX Session", filters={"demo_batch": self.batch}, pluck="name")
            executions = frappe.get_all("MX Session Execution", filters={"demo_batch": self.batch}, pluck="name")
            for doctype, parents in (("MX Session Attendance", executions), ("MX Session Student", sessions)):
                if parents:
                    frappe.db.delete(doctype, {"parent": ["in", parents]})
            for doctype in ("MX Teacher Hour Entry", "MX Lesson Consumption Entry", "MX Session Execution",
                            "MX Session", "MX Student", "MX Teacher", "MX Course", "MX Room"):
                names = frappe.get_all(doctype, filters={"demo_batch": self.batch}, pluck="name")
                if names:
                    frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
                frappe.db.delete(doctype, {"demo_batch": self.batch})
            frappe.db.commit()

    def master(self, doctype, **fields):
        return frappe.get_doc({"doctype": doctype, "enabled": 1, "demo_batch": self.batch, **fields}).insert()

    def execution(self, statuses, *, start=None, early_reason=None):
        start = start or now_datetime() - timedelta(hours=3)
        session = frappe.get_doc({
            "doctype": "MX Session", "course": self.course.name, "teacher": self.teacher.name,
            "room": self.room.name, "start_at": start, "end_at": start + timedelta(hours=1),
            "students": [{"student": student.name} for student in self.students[:len(statuses)]],
            "demo_batch": self.batch,
        }).insert().submit()
        execution = frappe.get_doc({
            "doctype": "MX Session Execution", "session": session.name,
            "early_completion_reason": early_reason,
            "attendance": [
                {"student": student.name, "attendance_status": status}
                for student, status in zip(self.students[:len(statuses)], statuses, strict=True)
            ],
        }).insert().submit()
        return session, execution

    def worker(self, operation, name, hold, field=None):
        process = subprocess.Popen(
            [sys.executable, "-m", "meixin_admin.tests.concurrent_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.workers.append(process)
        process.stdin.write(json.dumps({
            "site": frappe.local.site, "sites_path": os.path.abspath(frappe.local.sites_path),
            "operation": operation, "name": name, "hold": hold, "field": field,
        }) + "\n")
        process.stdin.flush()
        self.assertEqual(process.stdout.readline().strip(), "LOCKED" if hold else "READY")
        return process

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
        self.assertEqual(confirm_teaching(execution.name, start, end), name)
        with self.assertRaises(frappe.ValidationError):
            confirm_teaching(execution.name, start, end + timedelta(minutes=1))
        with self.assertRaises(frappe.PermissionError):
            entry.save()

    def test_cancel_revision_and_zero_minutes(self):
        session, execution = self.execution(["请假"])
        completed = execution.completed_at
        frappe.db.set_value("MX Session Execution", execution.name, "completed_at", None,
                            update_modified=False)
        with self.assertRaises(frappe.ValidationError):
            confirm_teaching(execution.name, taught=0, exception_reason="缺少完成时间")
        frappe.db.set_value("MX Session Execution", execution.name, "completed_at", completed,
                            update_modified=False)
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

    def test_short_duration_requires_manager_confirmation(self):
        session, execution = self.execution(["到课"])
        start = session.start_at + timedelta(minutes=20)
        end = start + timedelta(minutes=20)
        with self.assertRaises(frappe.PermissionError):
            confirm_teaching(execution.name, start, end)
        with self.assertRaises(frappe.ValidationError):
            confirm_teaching(execution.name, taught="unknown", exception_reason="错误输入")
        name = confirm_teaching(execution.name, start, end, exception_reason="提前结束并核实")
        entry = frappe.get_doc("MX Teacher Hour Entry", name)
        self.assertEqual(entry.effect_minutes, 20)
        self.assertEqual(entry.exception_reason, "提前结束并核实")
        self.assertEqual(confirm_teaching(execution.name, start, end,
                                          exception_reason="提前结束并核实"), name)

    def test_completed_at_cutoff_after_early_execution(self):
        start = now_datetime() - timedelta(minutes=30)
        session, execution = self.execution(["到课"], start=start, early_reason="提前完成测试")
        completed = get_datetime(execution.completed_at)
        actual_start = start + timedelta(minutes=5)
        with patch("meixin_admin.teacher_hours.now_datetime", return_value=completed + timedelta(minutes=2)):
            with self.assertRaises(frappe.ValidationError):
                confirm_teaching(execution.name, actual_start, completed + timedelta(seconds=1),
                                 exception_reason="完成后仍授课")
        self.assertEqual(frappe.db.count("MX Teacher Hour Entry", {"execution": execution.name}), 0)
        frappe.db.set_value("MX Session Execution", execution.name, "completed_at", None,
                            update_modified=False)
        with self.assertRaises(frappe.ValidationError):
            confirm_teaching(execution.name, actual_start, completed,
                             exception_reason="缺少完成时间")
        frappe.db.set_value("MX Session Execution", execution.name, "completed_at", completed,
                            update_modified=False)
        name = confirm_teaching(execution.name, actual_start, completed,
                                exception_reason="提前完成，核实截至完成时刻的授课")
        self.assertEqual(get_datetime(frappe.db.get_value("MX Teacher Hour Entry", name, "actual_end")),
                         completed)

    def test_fifteen_sixteen_minute_review_boundary(self):
        scheduler = frappe.get_doc({
            "doctype": "User", "email": f"m4-{uuid.uuid4().hex[:12]}@example.invalid",
            "first_name": "M4虚构排课员", "enabled": 1, "send_welcome_email": 0,
            "user_type": "System User", "roles": [{"role": "Meixin Scheduler"}],
        }).insert().name
        session, execution = self.execution(["到课"], start=now_datetime() - timedelta(hours=4))
        frappe.set_user(scheduler)
        name = confirm_teaching(execution.name, session.start_at,
                                session.end_at - timedelta(minutes=15))
        self.assertEqual(frappe.db.get_value("MX Teacher Hour Entry", name, "effect_minutes"), 45)
        frappe.set_user("Administrator")
        session, execution = self.execution(["到课"], start=now_datetime() - timedelta(hours=6))
        end = session.end_at - timedelta(minutes=16)
        frappe.set_user(scheduler)
        with self.assertRaises(frappe.PermissionError):
            confirm_teaching(execution.name, session.start_at, end)
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.PermissionError):
            confirm_teaching(execution.name, session.start_at, end)
        name = confirm_teaching(execution.name, session.start_at, end,
                                exception_reason="少于计划十六分钟，已核实")
        self.assertEqual(frappe.db.get_value("MX Teacher Hour Entry", name, "effect_minutes"), 44)

    def test_cross_midnight_actual_period(self):
        start = now_datetime().replace(hour=0, minute=0, second=0, microsecond=0)
        start -= timedelta(days=1, minutes=30)
        session, execution = self.execution(["到课"], start=start)
        actual_start, actual_end = start + timedelta(minutes=5), start + timedelta(minutes=50)
        self.assertNotEqual(actual_start.date(), actual_end.date())
        name = confirm_teaching(execution.name, actual_start, actual_end)
        self.assertEqual(frappe.db.get_value("MX Teacher Hour Entry", name, "effect_minutes"), 45)

    def test_substitute_and_coteaching_are_not_silently_recorded(self):
        session, execution = self.execution(["到课"])
        substitute = self.master("MX Teacher", teacher_name="M4虚构代课教师")
        with self.assertRaises(TypeError):
            confirm_teaching(execution.name, session.start_at, session.end_at,
                             teacher=substitute.name)
        entry = frappe.get_doc("MX Teacher Hour Entry", confirm_teaching(
            execution.name, session.start_at, session.end_at,
        ))
        self.assertEqual(entry.teacher, self.teacher.name)
        self.assertFalse(frappe.get_meta("MX Teacher Hour Entry").has_field("co_teacher"))

    def test_concurrent_same_request_returns_one_entry(self):
        session, execution = self.execution(["到课"])
        frappe.db.commit()
        self.committed = True

        field = {"start": str(session.start_at), "end": str(session.end_at)}
        first = self.worker("teacher_confirm", execution.name, True, field)
        second = self.worker("teacher_confirm", execution.name, False, field)
        second.stdin.write("GO\n")
        second.stdin.flush()
        self.assertEqual(second.stdout.readline().strip(), "ATTEMPT")
        first.stdin.write("GO\n")
        first.stdin.flush()
        self.assertEqual(first.stdout.readline().strip(), "ATTEMPT")
        results = [json.loads(process.stdout.readline().removeprefix("RESULT "))
                   for process in (first, second)]
        self.assertTrue(results[0]["ok"], results)
        if results[1]["ok"]:
            self.assertEqual(results[0]["name"], results[1]["name"])
        else:
            self.assertIn("已回滚", results[1]["message"])
        frappe.db.rollback()
        self.assertEqual(confirm_teaching(execution.name, session.start_at, session.end_at),
                         results[0]["name"])
        self.assertEqual(frappe.db.count("MX Teacher Hour Entry", {
            "execution": execution.name, "operation_type": "确认",
        }), 1)

    def test_confirm_cancel_race_keeps_legal_ledger(self):
        def race(first_operation, second_operation, session, execution):
            field = {"start": str(session.start_at), "end": str(session.end_at)}
            first = self.worker(first_operation, execution.name, True, field)
            second = self.worker(second_operation, execution.name, False, field)
            second.stdin.write("GO\n")
            second.stdin.flush()
            self.assertEqual(second.stdout.readline().strip(), "ATTEMPT")
            first.stdin.write("GO\n")
            first.stdin.flush()
            self.assertEqual(first.stdout.readline().strip(), "ATTEMPT")
            results = [json.loads(process.stdout.readline().removeprefix("RESULT "))
                       for process in (first, second)]
            self.assertTrue(results[0]["ok"], results)
            frappe.db.rollback()
            return results

        session, execution = self.execution(["到课"])
        frappe.db.commit()
        self.committed = True
        results = race("teacher_confirm", "execution_cancel", session, execution)
        if not results[1]["ok"]:
            self.assertIn("已回滚", results[1]["message"])
            frappe.get_doc("MX Session Execution", execution.name).cancel()
            frappe.db.commit()
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 2)
        self.assertEqual(sorted(frappe.get_all("MX Teacher Hour Entry", filters={"execution": execution.name},
                                        pluck="effect_minutes")), [-60, 60])

        session, execution = self.execution(["到课"], start=now_datetime() - timedelta(hours=6))
        frappe.db.commit()
        results = race("execution_cancel", "teacher_confirm", session, execution)
        self.assertFalse(results[1]["ok"], results)
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 2)
        self.assertEqual(frappe.db.count("MX Teacher Hour Entry", {"execution": execution.name}), 0)

    def test_cancel_failure_rolls_back_teacher_m2_and_m3(self):
        plan = frappe.get_doc({
            "doctype": "MX Package Plan", "plan_name": "M4虚构课包", "course": self.course.name,
            "standard_credits": 3, "standard_price": 100, "currency": "CNY", "enabled": 1,
            "demo_batch": self.batch,
        }).insert()
        package = frappe.get_doc({
            "doctype": "MX Student Package", "student": self.students[0].name,
            "package_plan": plan.name, "acquisition_type": "赠送", "request_id": uuid.uuid4().hex,
            "effective_from": (now_datetime() - timedelta(days=1)).date(), "demo_batch": self.batch,
        }).insert().submit()
        settings = frappe.get_single("MX Settings")
        settings.present_rule = "课消"
        settings.save()
        session, execution = self.execution(["到课"])
        original = confirm_teaching(execution.name, session.start_at, session.end_at)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 2)
        frappe.db.savepoint("m4_cancel_failure")
        with patch("meixin_admin.meixin_admin.doctype.mx_session_execution.mx_session_execution.reverse_execution",
                   side_effect=frappe.ValidationError("注入 M2 失败")):
            with self.assertRaises(frappe.ValidationError):
                execution.cancel()
        frappe.db.rollback(save_point="m4_cancel_failure")
        execution.reload()
        self.assertEqual(execution.docstatus, 1)
        self.assertEqual(frappe.db.count("MX Teacher Hour Entry", {"reversal_of": original}), 0)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 2)
        execution.cancel()
        effects = frappe.get_all("MX Teacher Hour Entry", filters={"execution": execution.name},
                                 pluck="effect_minutes", order_by="creation")
        self.assertEqual(effects, [60, -60])
        self.assertEqual(entitlements.locked_credit_balance(package.name), 3)
        amended = frappe.copy_doc(execution)
        amended.amended_from = execution.name
        amended.docstatus = 0
        for row in amended.attendance:
            row.consumption_rule_result = None
        amended.insert().submit()
        revised = confirm_teaching(amended.name, session.start_at,
                                   session.start_at + timedelta(minutes=50))
        self.assertEqual(frappe.db.get_value("MX Teacher Hour Entry", revised, "effect_minutes"), 50)
        self.assertEqual(frappe.db.count("MX Teacher Hour Entry", {"session": session.name}), 3)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 2)
