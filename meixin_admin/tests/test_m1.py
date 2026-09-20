"""真实 Frappe/MariaDB 集成验收；禁止导入后在业务站点执行。"""

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

from meixin_admin import api, demo
from meixin_admin.tests.run import assert_isolated_site


class TestM1(unittest.TestCase):
    def setUp(self):
        assert_isolated_site()
        frappe.db.rollback()
        frappe.set_user("Administrator")
        self.batch = "TEST-M1-" + uuid.uuid4().hex[:16]
        self.start = now_datetime().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=30)
        self.committed = False
        self.workers = []
        self.students = [self.master("MX Student", student_name=f"虚构测试学生{i}") for i in range(6)]
        self.teachers = [self.master("MX Teacher", teacher_name=f"虚构测试教师{i}") for i in range(2)]
        self.courses = [self.master("MX Course", course_name=f"虚构测试课程{i}", default_duration_minutes=60) for i in range(2)]
        self.rooms = [self.master("MX Room", room_name=f"虚构测试教室{i}", capacity=6) for i in range(2)]

    def tearDown(self):
        assert_isolated_site()
        for process in self.workers:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        frappe.set_user("Administrator")
        frappe.db.rollback()
        if self.committed:
            # 仅清理由本测试创建并显式提交的唯一批次，不清库、不调用 test runner。
            names = frappe.get_all("MX Session", filters={"demo_batch": self.batch}, pluck="name")
            if names:
                frappe.db.delete("MX Session Student", {"parent": ["in", names], "parenttype": "MX Session"})
            for doctype in ("MX Session", "MX Student", "MX Teacher", "MX Course", "MX Room"):
                batch_names = frappe.get_all(doctype, filters={"demo_batch": self.batch}, pluck="name")
                if batch_names:
                    frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", batch_names]})
                frappe.db.delete(doctype, {"demo_batch": self.batch})
            frappe.db.commit()

    def master(self, doctype, **fields):
        return frappe.get_doc({"doctype": doctype, "demo_batch": self.batch, "enabled": 1, **fields}).insert()

    def session(self, *, minutes=0, duration=60, students=None, teacher=None, room=None, course=None, insert=True):
        doc = frappe.get_doc({
            "doctype": "MX Session", "demo_batch": self.batch,
            "course": course or self.courses[0].name, "teacher": teacher or self.teachers[0].name,
            "room": room or self.rooms[0].name, "start_at": self.start + timedelta(minutes=minutes),
            "end_at": self.start + timedelta(minutes=minutes + duration),
            "students": [{"student": name} for name in (students if students is not None else [self.students[0].name])],
        })
        return doc.insert() if insert else doc

    def rejected(self, action, exception=frappe.ValidationError):
        savepoint = "mx_check_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            with self.assertRaises(exception) as caught:
                action()
            return str(caught.exception)
        finally:
            frappe.db.rollback(save_point=savepoint)

    def user(self, role=None):
        email = "mx-m1-" + uuid.uuid4().hex[:16] + "@example.invalid"
        return frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "隔离测试用户", "enabled": 1,
            "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": role}] if role else [],
        }).insert().name

    def test_01_normal_and_drafts_do_not_reserve(self):
        first, second = self.session(), self.session()
        self.assertEqual(first.docstatus, 0)
        second.submit()
        self.assertEqual(frappe.db.get_value("MX Session", second.name, "docstatus"), 1)
        self.rejected(first.submit)

    def test_02_teacher_conflict(self):
        self.session().submit()
        second = self.session(room=self.rooms[1].name, students=[self.students[1].name])
        self.assertIn("教师", self.rejected(second.submit))

    def test_03_room_conflict(self):
        self.session().submit()
        second = self.session(teacher=self.teachers[1].name, students=[self.students[1].name])
        self.assertIn("教室", self.rejected(second.submit))

    def test_04_student_conflict(self):
        self.session().submit()
        second = self.session(teacher=self.teachers[1].name, room=self.rooms[1].name)
        self.assertIn("学生", self.rejected(second.submit))

    def test_05_adjacent_periods_allowed(self):
        self.session().submit()
        self.session(minutes=60).submit()
        self.assertEqual(frappe.db.count("MX Session", {"demo_batch": self.batch, "docstatus": 1}), 2)

    def test_06_invalid_periods(self):
        for duration in (0, -60):
            self.assertIn("结束时间", self.rejected(lambda: self.session(duration=duration)))
        for field in ("start_at", "end_at"):
            candidate = self.session(insert=False)
            # None 不得被 Frappe get_datetime 转成当前时间而绕过必填。
            if field == "end_at":
                candidate.start_at = now_datetime() - timedelta(days=1)
            candidate.set(field, None)
            self.rejected(candidate.insert)

    def test_07_duplicate_and_empty_students(self):
        self.rejected(lambda: self.session(students=[self.students[0].name] * 2))
        self.rejected(lambda: self.session(students=[]))

    def test_08_capacity_and_positive_master_fields(self):
        self.rooms[0].capacity = 1
        self.rooms[0].save()
        self.rejected(lambda: self.session(students=[s.name for s in self.students[:2]]))
        self.rejected(lambda: self.master("MX Room", room_name="零容量", capacity=0))
        self.rejected(lambda: self.master("MX Course", course_name="负课时", default_duration_minutes=-1))

    def test_09_disabled_master_rechecked_at_submit(self):
        for master in (self.courses[0], self.teachers[0], self.rooms[0], self.students[0]):
            with self.subTest(doctype=master.doctype):
                draft = self.session()
                master.enabled = 0
                master.save()
                self.rejected(draft.submit)
                self.rejected(lambda: self.session())
                master.enabled = 1
                master.save()

    def test_10_cross_midnight_overlap_and_adjacency(self):
        self.start = self.start.replace(hour=23, minute=30)
        self.session().submit()
        self.rejected(self.session(minutes=45).submit)
        self.session(minutes=60).submit()

    def test_11_cancel_releases_but_keeps_record(self):
        first = self.session().submit()
        first.cancel()
        self.session().submit()
        self.assertEqual(frappe.db.get_value("MX Session", first.name, "docstatus"), 2)
        self.assertEqual(frappe.db.count("MX Session", {"demo_batch": self.batch}), 2)

    def test_12_submitted_fields_cannot_be_changed(self):
        doc = self.session().submit()
        for field, value in (("teacher", self.teachers[1].name), ("room", self.rooms[1].name),
                             ("course", self.courses[1].name), ("start_at", self.start + timedelta(minutes=5)),
                             ("end_at", self.start + timedelta(hours=2))):
            with self.subTest(field=field):
                doc.reload()
                doc.set(field, value)
                self.rejected(doc.save)
        doc.reload()
        doc.students[0].student = self.students[1].name
        self.rejected(doc.save)
        self.assertEqual(frappe.get_doc("MX Session", doc.name).students[0].student, self.students[0].name)

    def test_13_amendment_rechecks_current_conflicts(self):
        original = self.session().submit()
        original.cancel()
        self.session().submit()
        amended = frappe.copy_doc(original)
        amended.amended_from = original.name
        amended.docstatus = 0
        amended.insert()
        self.rejected(amended.submit)
        amended.reload()
        amended.start_at += timedelta(hours=1)
        amended.end_at += timedelta(hours=1)
        amended.save().submit()
        self.assertEqual(amended.amended_from, original.name)

    def test_14_failure_rolls_back_parent_children_and_audit(self):
        blocker = self.session().submit()
        before = frappe.db.count("MX Session", {"demo_batch": self.batch})
        savepoint = "mx_transaction_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        attempted = self.session()
        attempted_name = attempted.name
        try:
            with self.assertRaises(frappe.ValidationError):
                attempted.submit()
        finally:
            frappe.db.rollback(save_point=savepoint)
        self.assertEqual(frappe.db.count("MX Session", {"demo_batch": self.batch}), before)
        self.assertFalse(frappe.db.exists("MX Session", attempted_name))
        self.assertEqual(frappe.db.count("MX Session Student", {"parent": attempted_name}), 0)
        self.assertEqual(frappe.db.count("Version", {"ref_doctype": "MX Session", "docname": attempted_name}), 0)
        self.assertEqual(frappe.db.get_value("MX Session", blocker.name, "docstatus"), 1)

    def test_15_future_session_prevents_disable_and_capacity_shrink(self):
        session = self.session(students=[s.name for s in self.students[:2]]).submit()
        for master in (self.students[0], self.teachers[0], self.courses[0], self.rooms[0]):
            with self.subTest(doctype=master.doctype):
                master.enabled = 0
                self.rejected(master.save)
                master.reload()
                self.assertEqual(master.enabled, 1)
        self.rooms[0].capacity = 1
        self.rejected(self.rooms[0].save)
        self.rooms[0].reload()
        self.assertEqual(self.rooms[0].capacity, 6)
        session.cancel()
        self.rooms[0].capacity = 1
        self.rooms[0].save()
        self.students[0].enabled = 0
        self.students[0].save()

    def test_16_duplicate_names_phones_and_teacher_user_uniqueness(self):
        for _ in range(2):
            self.master("MX Student", student_name="同名虚构学生", guardian_phone="演示专用非电话", guardian_name="同一虚构监护人")
        teacher_user = self.user()
        self.teachers[0].user = teacher_user
        self.teachers[0].save()
        self.teachers[1].user = teacher_user
        self.rejected(self.teachers[1].save)

    def test_17_scheduler_permissions_and_real_writes(self):
        user = self.user("Meixin Scheduler")
        frappe.set_user(user)
        self.master("MX Student", student_name="教务新建虚构学生")
        session = self.session().submit()
        self.rejected(session.cancel, frappe.PermissionError)
        self.rejected(lambda: frappe.delete_doc("MX Student", self.students[5].name), frappe.PermissionError)
        settings = frappe.get_single("MX Settings")
        self.rejected(lambda: settings.check_permission("read"), frappe.PermissionError)
        self.rejected(settings.save, frappe.PermissionError)
        self.assertFalse(frappe.has_permission("MX Student", "export"))
        self.assertFalse(frappe.has_permission("Role", "write"))
        self.assertFalse(frappe.has_permission("User", "create"))
        self.rejected(lambda: demo.initialize(), frappe.PermissionError)
        self.rejected(lambda: demo.preview_cleanup(), frappe.PermissionError)
        self.assertTrue(api.get_context()["time_zone"])

    def test_18_manager_can_manage_settings_and_cancel(self):
        user = self.user("Meixin Manager")
        frappe.set_user(user)
        settings = frappe.get_single("MX Settings")
        settings.institution_name = "虚构测试机构"
        settings.save()
        self.session().submit().cancel()
        self.assertEqual(api.get_context()["institution_name"], "虚构测试机构")

    def test_19_guest_and_user_without_business_role_denied(self):
        from frappe.client import get, get_list, insert

        outsider = self.user("System Manager")
        session = self.session()
        for user in ("Guest", outsider):
            with self.subTest(user=user):
                frappe.set_user(user)
                for doctype, name in (("MX Student", self.students[0].name), ("MX Teacher", self.teachers[0].name),
                                      ("MX Course", self.courses[0].name), ("MX Room", self.rooms[0].name),
                                      ("MX Session", session.name), ("MX Settings", "MX Settings")):
                    self.rejected(lambda: get(doctype, name), frappe.PermissionError)
                    if doctype != "MX Settings":
                        self.rejected(lambda: get_list(doctype), frappe.PermissionError)
                self.rejected(lambda: insert({"doctype": "MX Student", "student_name": "越权创建"}), frappe.PermissionError)
                self.rejected(lambda: api.get_events(str(self.start), str(self.start + timedelta(days=1))), frappe.PermissionError)
                self.rejected(api.get_context, frappe.PermissionError)

    def test_20_calendar_filters_status_and_boundary(self):
        confirmed = self.session().submit()
        draft = self.session(minutes=60, teacher=self.teachers[1].name, room=self.rooms[1].name)
        canceled = self.session(minutes=120).submit().cancel()
        start, end = str(self.start), str(self.start + timedelta(days=1))
        rows = api.get_events(start, end)
        relevant = {row["name"]: row for row in rows if row["name"] in (confirmed.name, draft.name, canceled.name)}
        self.assertEqual(set(relevant), {confirmed.name, draft.name})
        self.assertNotEqual(relevant[confirmed.name]["color"], relevant[draft.name]["color"])
        self.assertIn("不占用", relevant[draft.name]["title"])
        self.assertEqual([row["name"] for row in api.get_events(start, end, {"teacher": self.teachers[1].name})], [draft.name])
        self.assertEqual([row["name"] for row in api.get_events(start, end, {"room": self.rooms[0].name})], [confirmed.name])
        self.assertNotIn(confirmed.name, [row["name"] for row in api.get_events(str(self.start + timedelta(hours=1)), end)])
        self.rejected(lambda: api.get_events(start, end, {"owner": "Administrator"}))

    def test_21_unreadable_conflict_details_not_disclosed(self):
        hidden = self.session(room=self.rooms[1].name).submit()
        user = self.user("Meixin Scheduler")
        frappe.get_doc({"doctype": "User Permission", "user": user, "allow": "MX Room", "for_value": self.rooms[0].name,
                        "apply_to_all_doctypes": 1}).insert()
        frappe.set_user(user)
        self.assertFalse(frappe.has_permission("MX Session", "read", doc=hidden))
        rows = api.get_events(str(self.start), str(self.start + timedelta(days=1)))
        self.assertNotIn(hidden.name, [row["name"] for row in rows])
        candidate = self.session(students=[self.students[1].name])
        frappe.local.message_log = []
        message = self.rejected(candidate.submit)
        self.assertIn("无权查看", message)
        self.assertNotIn(hidden.name, message)
        self.assertNotIn(str(self.start.date()), message)
        messages = json.dumps(frappe.local.message_log, ensure_ascii=False)
        self.assertNotIn(hidden.name, messages)
        self.assertNotIn(self.rooms[1].name, messages)

    def test_22_demo_idempotent_and_preview_read_only(self):
        batch = self.batch + "-DEMO"
        first = demo.initialize(batch=batch, start_date=str(self.start.date()))
        second = demo.initialize(batch=batch, start_date=str((self.start + timedelta(days=7)).date()))
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["records"], second["records"])
        self.assertEqual(first["counts"], {"MX Student": 6, "MX Teacher": 2, "MX Course": 2, "MX Room": 2, "MX Session": 3})
        before = {dt: frappe.db.count(dt, {"demo_batch": batch}) for dt in demo.EXPECTED}
        preview = demo.preview_cleanup(batch)
        self.assertTrue(preview["preview_only"])
        self.assertEqual(before, preview["counts"])
        self.assertEqual(len(preview["referencing_sessions"]), 3)
        self.assertEqual(before, {dt: frappe.db.count(dt, {"demo_batch": batch}) for dt in demo.EXPECTED})
        for name in first["records"]["MX Teacher"]:
            self.assertFalse(frappe.db.get_value("MX Teacher", name, "user"))
            self.assertFalse(frappe.db.get_value("MX Teacher", name, "phone"))

    def test_23_demo_partial_batch_rejected_and_failure_atomic(self):
        self.rejected(lambda: demo.initialize(batch=self.batch))
        batch = self.batch + "-FAIL"
        from meixin_admin.meixin_admin.doctype.mx_session.mx_session import MXSession

        with patch.object(MXSession, "before_submit", side_effect=frappe.ValidationError("测试注入失败")):
            with self.assertRaises(frappe.ValidationError):
                demo.initialize(batch=batch)
        self.assertTrue(all(frappe.db.count(dt, {"demo_batch": batch}) == 0 for dt in demo.EXPECTED))

    def worker(self, operation, name, *, hold=False, field=None, value=None):
        process = subprocess.Popen(
            [sys.executable, "-m", "meixin_admin.tests.concurrent_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.workers.append(process)
        output = queue.Queue()

        def read_lines():
            for line in process.stdout:
                output.put(line.strip())

        threading.Thread(target=read_lines, daemon=True).start()
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
                    self.fail(f"并发进程在 {expected} 前退出：{process.stderr.read()[-2000:]}")
                continue
            if line.startswith(expected):
                return line
        self.fail(f"等待并发进程 {expected} 超时；未将本项标为通过。")

    def command_worker(self, worker, command="GO"):
        worker[0].stdin.write(command + "\n")
        worker[0].stdin.flush()

    def concurrent_pair(self, first, second):
        self.read_worker(first, "LOCKED")
        self.read_worker(second, "READY")
        self.command_worker(second)
        self.read_worker(second, "ATTEMPT")
        # 第二个进程已发起保存，第一进程仍持有数据库事务锁。
        time.sleep(0.5)
        self.assertIsNone(second[0].poll(), "第二个写入未等待排课锁。")
        self.assertTrue(second[1].empty(), "第二个写入在锁释放前已完成。")
        self.command_worker(first)
        results = [json.loads(self.read_worker(worker, "RESULT ").removeprefix("RESULT ")) for worker in (first, second)]
        for worker in (first, second):
            worker[0].wait(timeout=10)
        frappe.db.rollback()  # 丢弃父连接旧快照，重新读取子进程已提交结果。
        return results

    def test_24_concurrent_conflicting_submissions_only_one_succeeds(self):
        first, second = self.session(), self.session()
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(self.worker("submit", first.name, hold=True), self.worker("submit", second.name))
        self.assertEqual([result["ok"] for result in results], [True, False], results)
        self.assertEqual(results[1]["exception"], "ValidationError", results)
        self.assertEqual(frappe.db.count("MX Session", {"demo_batch": self.batch, "docstatus": 1}), 1)
        self.assertEqual(frappe.db.get_value("MX Session", second.name, "docstatus"), 0)
        self.assertEqual(len(frappe.get_doc("MX Session", second.name).students), 1)

    def test_25_submit_serializes_with_master_disable(self):
        session = self.session()
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(self.worker("submit", session.name, hold=True), self.worker("teacher", self.teachers[0].name, field="enabled", value=0))
        self.assertEqual([result["ok"] for result in results], [True, False], results)
        self.assertEqual(frappe.db.get_value("MX Teacher", self.teachers[0].name, "enabled"), 1)

    def test_26_submit_serializes_with_capacity_shrink(self):
        session = self.session(students=[s.name for s in self.students[:2]])
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(self.worker("submit", session.name, hold=True), self.worker("room", self.rooms[0].name, field="capacity", value=1))
        self.assertEqual([result["ok"] for result in results], [True, False], results)
        self.assertEqual(frappe.db.get_value("MX Room", self.rooms[0].name, "capacity"), 6)

    def test_27_cancel_cannot_rewrite_confirmed_history(self):
        session = self.session().submit()
        for field, value in (("teacher", self.teachers[1].name), ("room", self.rooms[1].name),
                             ("course", self.courses[1].name), ("start_at", self.start + timedelta(minutes=5)),
                             ("end_at", self.start + timedelta(hours=2))):
            with self.subTest(field=field):
                session.reload()
                session.set(field, value)
                self.rejected(session.cancel)
                self.assertEqual(frappe.db.get_value("MX Session", session.name, "docstatus"), 1)
        session.reload()
        session.students[0].student = self.students[1].name
        self.rejected(session.cancel)
        session.reload()
        session.cancel()
        self.assertEqual(frappe.get_doc("MX Session", session.name).students[0].student, self.students[0].name)
