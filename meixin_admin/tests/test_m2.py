"""M2 execution, attendance and immutable-consumption integration tests."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from meixin_admin import consumption, execution as execution_api
from meixin_admin.tests.run import assert_isolated_site

RULE_FIELDS = ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule")


class TestM2(unittest.TestCase):
    def setUp(self):
        assert_isolated_site()
        frappe.db.rollback()
        frappe.set_user("Administrator")
        self.batch = "TEST-M2-" + uuid.uuid4().hex[:16]
        self.workers = []
        self.committed = False
        self.set_rules("不课消")
        self.students = [self.master("MX Student", student_name=f"M2虚构学生{i}") for i in range(4)]
        self.teacher = self.master("MX Teacher", teacher_name="M2虚构教师")
        self.course = self.master("MX Course", course_name="M2虚构课程", default_duration_minutes=60)
        self.room = self.master("MX Room", room_name="M2虚构教室", capacity=8)

    def tearDown(self):
        assert_isolated_site()
        for process in self.workers:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        frappe.set_user("Administrator")
        frappe.db.rollback()
        if self.committed:
            sessions = frappe.get_all("MX Session", filters={"demo_batch": self.batch}, pluck="name")
            executions = frappe.get_all("MX Session Execution", filters={"demo_batch": self.batch}, pluck="name")
            for doctype, parents in (("MX Session Attendance", executions), ("MX Session Student", sessions)):
                if parents:
                    frappe.db.delete(doctype, {"parent": ["in", parents]})
            for doctype in ("MX Lesson Consumption Entry", "MX Session Execution", "MX Session",
                            "MX Student", "MX Teacher", "MX Course", "MX Room"):
                names = frappe.get_all(doctype, filters={"demo_batch": self.batch}, pluck="name")
                if names:
                    frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
                frappe.db.delete(doctype, {"demo_batch": self.batch})
            for field in RULE_FIELDS:
                frappe.db.set_single_value("MX Settings", field, "未配置")
            frappe.db.commit()

    def set_rules(self, default=None, **rules):
        settings = frappe.get_single("MX Settings")
        if default:
            for field in RULE_FIELDS:
                settings.set(field, default)
        for field, value in rules.items():
            settings.set(field, value)
        settings.save()

    def master(self, doctype, **fields):
        if doctype == "MX Student":
            fields.setdefault("guardian_phone", "00000000000")
        return frappe.get_doc({"doctype": doctype, "demo_batch": self.batch, "enabled": 1, **fields}).insert()

    def session(self, *, students=None, start=None):
        start = start or now_datetime() + timedelta(hours=1)
        return frappe.get_doc({
            "doctype": "MX Session", "demo_batch": self.batch,
            "course": self.course.name, "teacher": self.teacher.name, "room": self.room.name,
            "start_at": start, "end_at": start + timedelta(hours=1),
            "students": [{"student": student.name} for student in (students or self.students[:1])],
        }).insert().submit()

    def execution(self, session, statuses=None, reason="隔离测试提前完成"):
        statuses = statuses or ["到课"] * len(session.students)
        rows = []
        for source, status in zip(session.students, statuses, strict=True):
            rows.append({
                "student": source.student, "attendance_status": status,
                "notes": "其他状态说明" if status == "其他" else None,
            })
        return frappe.get_doc({
            "doctype": "MX Session Execution", "session": session.name,
            "attendance": rows, "early_completion_reason": reason,
        }).insert()

    def rejected(self, action, exception=frappe.ValidationError):
        savepoint = "mx_m2_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            with self.assertRaises(exception) as caught:
                action()
            return str(caught.exception)
        finally:
            frappe.db.rollback(save_point=savepoint)

    def user(self, role=None):
        email = "mx-m2-" + uuid.uuid4().hex[:16] + "@example.invalid"
        return frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "M2隔离用户", "enabled": 1,
            "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": role}] if role else [],
        }).insert().name

    def test_01_unconfigured_rule_blocks_without_partial_entries(self):
        self.set_rules(present_rule="未配置")
        execution = self.execution(self.session())
        self.assertIn("尚未配置", self.rejected(execution.submit))
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 0)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)

    def test_02_mixed_attendance_freezes_rules_and_keeps_zero_entries(self):
        self.set_rules(present_rule="课消", leave_rule="不课消", absent_rule="课消", other_rule="不课消")
        execution = self.execution(self.session(students=self.students), ["到课", "请假", "缺勤", "其他"]).submit()
        entries = frappe.get_all(
            "MX Lesson Consumption Entry", filters={"execution": execution.name, "operation_type": "决定"},
            fields=["student", "outcome", "rule_result", "effect"], order_by="student",
        )
        self.assertEqual(len(entries), 4)
        self.assertEqual(sorted(row.effect for row in entries), [0, 0, 1, 1])
        self.assertEqual(
            {row.attendance_status: row.consumption_rule_result for row in execution.attendance},
            {"到课": "课消", "请假": "不课消", "缺勤": "课消", "其他": "不课消"},
        )

    def test_03_early_completion_requires_manager_reason(self):
        session = self.session()
        manager_doc = self.execution(session, reason="")
        self.assertIn("提前完成原因", self.rejected(manager_doc.submit))
        manager_doc.reload()
        manager_doc.early_completion_reason = "学生临时提前完成课程"
        manager_doc.save()
        scheduler = self.user("Meixin Scheduler")
        frappe.set_user(scheduler)
        self.assertIn("只有美心管理员", self.rejected(manager_doc.submit))
        frappe.set_user("Administrator")
        manager_doc.reload().submit()
        self.assertEqual(manager_doc.completed_by, "Administrator")
        self.assertTrue(manager_doc.completed_at)

    def test_04_scheduler_can_complete_after_planned_end(self):
        session = self.session(start=now_datetime() - timedelta(hours=2))
        execution = self.execution(session, reason="")
        scheduler = self.user("Meixin Scheduler")
        frappe.set_user(scheduler)
        execution.reload().submit()
        self.assertEqual(execution.completed_by, scheduler)

    def test_05_roster_and_other_note_are_mandatory(self):
        session = self.session(students=self.students[:2])
        missing = self.execution(session, ["到课", "请假"])
        missing.attendance.pop()
        self.assertIn("完全一致", self.rejected(missing.save))
        other = self.execution(session, ["到课", "其他"])
        other.attendance[1].notes = ""
        other.save()
        self.assertIn("必须填写说明", self.rejected(other.submit))

    def test_06_only_one_active_execution_and_decisions_are_idempotent(self):
        session = self.session()
        first = self.execution(session).submit()
        second = self.execution(session)
        self.assertIn("已有已完成", self.rejected(second.submit))
        entries = frappe.get_all("MX Lesson Consumption Entry", filters={"execution": first.name})
        self.assertEqual(len(entries), 1)

    def test_07_cancel_adds_reversals_for_plus_one_and_zero(self):
        self.set_rules(present_rule="课消", leave_rule="不课消")
        execution = self.execution(self.session(students=self.students[:2]), ["到课", "请假"]).submit()
        execution.cancel()
        rows = frappe.get_all(
            "MX Lesson Consumption Entry", filters={"execution": execution.name},
            fields=["name", "effect", "operation_type", "reversal_of"], order_by="creation",
        )
        self.assertEqual([row.effect for row in rows], [1, 0, -1, 0])
        self.assertTrue(all(row.reversal_of for row in rows[2:]))
        self.rejected(lambda: frappe.delete_doc("MX Lesson Consumption Entry", rows[0].name))

    def test_08_revision_is_manager_only_and_preserves_history(self):
        original = self.execution(self.session()).submit()
        original.cancel()
        scheduler = self.user("Meixin Scheduler")
        frappe.set_user(scheduler)
        self.assertFalse(frappe.has_permission("MX Session Execution", "amend", doc=original))
        self.assertIn("只有美心管理员", self.rejected(
            lambda: self.execution(frappe.get_doc("MX Session", original.session)).insert(),
            frappe.PermissionError,
        ))
        frappe.set_user("Administrator")
        amended = frappe.copy_doc(original)
        amended.amended_from = original.name
        amended.docstatus = 0
        amended.early_completion_reason = "修订后重新确认"
        for row in amended.attendance:
            row.consumption_rule_result = None
        amended.insert().submit()
        self.assertEqual(amended.amended_from, original.name)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": original.name}), 2)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": amended.name}), 1)

    def test_09_course_cancel_requires_rule_and_keeps_zero(self):
        session = self.session()
        self.set_rules(session_cancel_rule="未配置")
        self.assertIn("尚未配置", self.rejected(session.cancel))
        session.reload()
        self.set_rules(session_cancel_rule="不课消")
        session.cancel()
        entry = frappe.get_doc("MX Lesson Consumption Entry", {
            "session": session.name, "outcome": "课程取消",
        })
        self.assertEqual((entry.effect, entry.rule_result), (0, "不课消"))

    def test_10_completed_execution_blocks_session_cancel_until_reversed(self):
        session = self.session()
        execution = self.execution(session).submit()
        self.assertIn("必须先", self.rejected(session.cancel))
        execution.reload().cancel()
        session.reload().cancel()
        self.assertEqual(session.docstatus, 2)

    def test_11_manual_reversal_is_manager_only_and_unique(self):
        self.set_rules(present_rule="课消")
        execution = self.execution(self.session()).submit()
        entry = frappe.db.get_value(
            "MX Lesson Consumption Entry", {"execution": execution.name, "operation_type": "决定"}, "name"
        )
        scheduler = self.user("Meixin Scheduler")
        frappe.set_user(scheduler)
        self.rejected(lambda: consumption.manual_reverse(entry, "无权撤销"), frappe.PermissionError)
        frappe.set_user("Administrator")
        reversal = consumption.manual_reverse(entry, "录入错误")
        self.assertEqual(frappe.db.get_value("MX Lesson Consumption Entry", reversal, "effect"), -1)
        self.assertEqual(consumption.manual_reverse(entry, "录入错误"), reversal)

    def test_12_ledger_rejects_direct_insert_update_and_delete(self):
        session = self.session()
        values = consumption._entry_values(
            session, self.students[0].name, "到课", "不课消", "决定", 0,
            "direct:" + uuid.uuid4().hex,
        )
        self.rejected(lambda: frappe.get_doc(values).insert(ignore_permissions=True), frappe.PermissionError)
        entry = self.execution(session).submit()
        name = frappe.db.get_value("MX Lesson Consumption Entry", {"execution": entry.name}, "name")
        doc = frappe.get_doc("MX Lesson Consumption Entry", name)
        doc.reason = "试图修改"
        self.rejected(lambda: doc.save(ignore_permissions=True), frappe.PermissionError)
        self.rejected(lambda: frappe.delete_doc("MX Lesson Consumption Entry", name, ignore_permissions=True))

    def test_13_rule_changes_do_not_rewrite_history(self):
        self.set_rules(present_rule="课消")
        execution = self.execution(self.session()).submit()
        self.set_rules(present_rule="不课消")
        execution.reload()
        entry = frappe.get_doc("MX Lesson Consumption Entry", {"execution": execution.name})
        self.assertEqual(execution.attendance[0].consumption_rule_result, "课消")
        self.assertEqual((entry.rule_result, entry.effect), ("课消", 1))

    def test_14_failure_rolls_back_execution_attendance_and_entries(self):
        execution = self.execution(self.session(students=self.students[:2]), ["到课", "请假"])
        real_insert = consumption.insert_idempotent
        calls = 0

        def fail_second(values):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise frappe.ValidationError("测试注入流水失败")
            return real_insert(values)

        with patch("meixin_admin.consumption.insert_idempotent", side_effect=fail_second):
            self.rejected(execution.submit)
        execution.reload()
        self.assertEqual(execution.docstatus, 0)
        self.assertTrue(all(not row.consumption_rule_result for row in execution.attendance))
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)

    def worker(self, operation, name=None, *, hold=False, field=None, value=None):
        process = subprocess.Popen(
            [sys.executable, "-m", "meixin_admin.tests.concurrent_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.workers.append(process)
        output = queue.Queue()
        threading.Thread(
            target=lambda: [output.put(line.strip()) for line in process.stdout], daemon=True,
        ).start()
        process.stdin.write(json.dumps({
            "site": frappe.local.site, "sites_path": os.path.abspath(frappe.local.sites_path),
            "operation": operation, "name": name, "hold": hold, "field": field, "value": value,
        }) + "\n")
        process.stdin.flush()
        return process, output

    def read_worker(self, worker, expected, timeout=30):
        process, output = worker
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = output.get(timeout=min(1, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if process.poll() is not None:
                    self.fail(f"并发进程提前退出：{process.stderr.read()[-2000:]}")
                continue
            if line.startswith(expected):
                return line
        self.fail(f"等待并发进程 {expected} 超时。")

    def concurrent_pair(self, first, second):
        self.read_worker(first, "LOCKED")
        self.read_worker(second, "READY")
        second[0].stdin.write("GO\n")
        second[0].stdin.flush()
        self.read_worker(second, "ATTEMPT")
        time.sleep(0.5)
        self.assertIsNone(second[0].poll())
        first[0].stdin.write("GO\n")
        first[0].stdin.flush()
        results = [json.loads(self.read_worker(w, "RESULT ").removeprefix("RESULT ")) for w in (first, second)]
        for worker in (first, second):
            worker[0].wait(timeout=10)
        frappe.db.rollback()
        return results

    def test_15_concurrent_double_completion_creates_one_active_execution(self):
        session = self.session()
        first, second = self.execution(session), self.execution(session)
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker("execution_submit", first.name, hold=True),
            self.worker("execution_submit", second.name),
        )
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        self.assertEqual(frappe.db.count("MX Session Execution", {"session": session.name, "docstatus": 1}), 1)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"session": session.name, "operation_type": "决定"}), 1)

    def test_16_concurrent_completion_and_session_cancel_leave_one_legal_result(self):
        session = self.session()
        execution = self.execution(session)
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker("execution_submit", execution.name, hold=True),
            self.worker("session_cancel", session.name),
        )
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        self.assertEqual(frappe.db.get_value("MX Session", session.name, "docstatus"), 1)
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 1)

    def test_17_concurrent_rule_change_waits_and_does_not_rewrite_frozen_result(self):
        self.set_rules(present_rule="课消")
        execution = self.execution(self.session())
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker("execution_submit", execution.name, hold=True),
            self.worker("settings", hold=False, field="present_rule", value="不课消"),
        )
        self.assertEqual([row["ok"] for row in results], [True, True], results)
        entry = frappe.get_all(
            "MX Lesson Consumption Entry", filters={"execution": execution.name},
            fields=["rule_result", "effect"], limit=1,
        )[0]
        self.assertEqual((entry.rule_result, entry.effect), ("课消", 1))
        self.assertEqual(frappe.db.get_single_value("MX Settings", "present_rule"), "不课消")

    def test_18_execution_form_api_populates_roster_and_returns_existing_draft(self):
        session = self.session(students=self.students[:2])
        payload = execution_api.make_execution(session.name)
        self.assertTrue(payload.get("__islocal"))
        self.assertEqual([row.student for row in payload.attendance], [s.name for s in self.students[:2]])
        draft = frappe.get_doc(payload).insert()
        status = execution_api.get_session_execution_status(session.name)
        self.assertEqual(status, {"status": "待上课", "execution": draft.name})
        existing = execution_api.make_execution(session.name)
        self.assertEqual(existing.name, draft.name)
        self.assertFalse(existing.get("__islocal"))


if __name__ == "__main__":
    unittest.main()
