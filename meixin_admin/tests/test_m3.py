"""M3 package, payment and lesson-credit integration tests."""
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
from frappe.utils import now_datetime, nowdate

from meixin_admin import consumption
from meixin_admin import entitlements
from meixin_admin.tests.run import assert_isolated_site


class TestM3Schema(unittest.TestCase):
    def setUp(self):
        assert_isolated_site()
        frappe.db.rollback()
        frappe.set_user("Administrator")
        self.batch = "TEST-M3-" + uuid.uuid4().hex[:16]
        self.workers = []
        self.committed = False
        self.student = frappe.get_doc({
            "doctype": "MX Student", "student_name": "M3虚构学生",
            "guardian_phone": "00000000000", "enabled": 1, "demo_batch": self.batch,
        }).insert()
        self.course = frappe.get_doc({
            "doctype": "MX Course", "course_name": "M3虚构课程",
            "default_duration_minutes": 60, "enabled": 1, "demo_batch": self.batch,
        }).insert()
        self.teacher = frappe.get_doc({
            "doctype": "MX Teacher", "teacher_name": "M3虚构教师",
            "enabled": 1, "demo_batch": self.batch,
        }).insert()
        self.room = frappe.get_doc({
            "doctype": "MX Room", "room_name": "M3虚构教室", "capacity": 8,
            "enabled": 1, "demo_batch": self.batch,
        }).insert()
        self.plan = frappe.get_doc({
            "doctype": "MX Package Plan", "plan_name": "M3虚构20课时包",
            "course": self.course.name, "standard_credits": 20,
            "standard_price": 2000, "currency": "CNY", "enabled": 1,
            "demo_batch": self.batch,
        }).insert()

    def tearDown(self):
        for process in self.workers:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        frappe.set_user("Administrator")
        frappe.db.rollback()
        if self.committed:
            sessions = frappe.get_all("MX Session", filters={"demo_batch": self.batch}, pluck="name")
            executions = frappe.get_all(
                "MX Session Execution", filters={"demo_batch": self.batch}, pluck="name"
            )
            for doctype, parents in (("MX Session Attendance", executions), ("MX Session Student", sessions)):
                if parents:
                    frappe.db.delete(doctype, {"parent": ["in", parents]})
            doctypes = (
                "MX Lesson Credit Entry", "MX Payment", "MX Student Package", "MX Package Plan",
                "MX Lesson Consumption Entry", "MX Session Execution", "MX Session",
                "MX Student", "MX Teacher", "MX Course", "MX Room",
            )
            for doctype in doctypes:
                names = frappe.get_all(doctype, filters={"demo_batch": self.batch}, pluck="name")
                if names:
                    frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
                frappe.db.delete(doctype, {"demo_batch": self.batch})
            frappe.db.commit()

    def package(self, acquisition_type="购买"):
        return frappe.get_doc({
            "doctype": "MX Student Package", "student": self.student.name,
            "package_plan": self.plan.name, "acquisition_type": acquisition_type,
            "effective_from": nowdate(), "demo_batch": self.batch,
        }).insert()

    def user(self, role):
        return frappe.get_doc({
            "doctype": "User", "email": f"mx-m3-{uuid.uuid4().hex[:16]}@example.invalid",
            "first_name": "M3隔离用户", "enabled": 1, "send_welcome_email": 0,
            "user_type": "System User", "roles": [{"role": role}],
        }).insert().name

    def payment(self, package, amount, *, request_id=None, paid_at=None):
        return frappe.get_doc({
            "doctype": "MX Payment", "student_package": package.name,
            "operation_type": "收款", "amount": amount, "currency": "CNY",
            "payment_method": "现金", "paid_at": paid_at or now_datetime(),
            "request_id": request_id, "demo_batch": self.batch,
        }).insert()

    def session(self):
        start = now_datetime() + timedelta(hours=1)
        return frappe.get_doc({
            "doctype": "MX Session", "course": self.course.name,
            "teacher": self.teacher.name, "room": self.room.name,
            "start_at": start, "end_at": start + timedelta(hours=1),
            "students": [{"student": self.student.name}], "demo_batch": self.batch,
        }).insert().submit()

    def execution(self, status="到课"):
        session = self.session()
        return frappe.get_doc({
            "doctype": "MX Session Execution", "session": session.name,
            "attendance": [{"student": self.student.name, "attendance_status": status}],
            "early_completion_reason": "M3 隔离测试提前完成",
        }).insert()

    def set_rule(self, value):
        settings = frappe.get_single("MX Settings")
        settings.present_rule = value
        settings.save()

    def rejected(self, action, exception=frappe.ValidationError):
        savepoint = "mx_m3_" + uuid.uuid4().hex[:12]
        frappe.db.savepoint(savepoint)
        try:
            with self.assertRaises(exception) as caught:
                action()
            return str(caught.exception)
        finally:
            frappe.db.rollback(save_point=savepoint)

    def test_01_package_snapshots_and_system_request_id(self):
        package = self.package()
        self.assertEqual(package.plan_name_snapshot, self.plan.plan_name)
        self.assertEqual(package.course, self.course.name)
        self.assertEqual(package.credits_granted, 20)
        self.assertEqual(package.deal_amount, 2000)
        self.assertEqual(len(package.request_id), 32)

    def test_02_scheduler_cannot_manage_plan_or_create_gift(self):
        scheduler = self.user("Meixin Scheduler")
        frappe.set_user(scheduler)
        denied_plan = frappe.get_doc({
            "doctype": "MX Package Plan", "plan_name": "越权课包",
            "course": self.course.name, "standard_credits": 1,
            "standard_price": 100, "currency": "CNY", "enabled": 1,
        })
        with self.assertRaises(frappe.PermissionError):
            denied_plan.insert()
        with self.assertRaises(frappe.PermissionError):
            self.package("赠送")

    def test_03_payment_derives_cash_effect_and_request_id(self):
        package = self.package().submit()
        payment = frappe.get_doc({
            "doctype": "MX Payment", "student_package": package.name,
            "operation_type": "收款", "amount": 500, "currency": "CNY",
            "payment_method": "现金", "paid_at": now_datetime(), "demo_batch": self.batch,
        }).insert()
        self.assertEqual(payment.student, self.student.name)
        self.assertEqual(payment.cash_effect, 500)
        self.assertEqual(len(payment.request_id), 32)

    def test_04_credit_entry_rejects_direct_insert(self):
        package = self.package().submit()
        entry = frappe.get_doc({
            "doctype": "MX Lesson Credit Entry", "student": self.student.name,
            "student_name_snapshot": self.student.student_name,
            "student_package": package.name, "package_plan": self.plan.name,
            "plan_name_snapshot": self.plan.plan_name, "course": self.course.name,
            "course_name_snapshot": self.course.course_name,
            "operation_type": "购买授予", "effect": 20,
            "idempotency_key": "test:" + uuid.uuid4().hex, "demo_batch": self.batch,
        })
        with self.assertRaises(frappe.PermissionError):
            entry.insert(ignore_permissions=True)

    def test_05_purchase_stays_pending_without_credit(self):
        package = self.package().submit()
        self.assertFalse(package.activated_at)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 0)

    def test_06_scheduler_full_payment_grants_once(self):
        package = self.package().submit()
        scheduler = self.user("Meixin Scheduler")
        frappe.set_user(scheduler)
        payment = self.payment(package, 2000).submit()
        package.reload()
        entries = frappe.get_all(
            "MX Lesson Credit Entry", filters={"student_package": package.name},
            fields=["operation_type", "effect", "idempotency_key"],
        )
        self.assertEqual(payment.handled_by, scheduler)
        self.assertTrue(package.activated_at)
        self.assertEqual(
            [(row.operation_type, row.effect, row.idempotency_key) for row in entries],
            [("购买授予", 20, f"package-grant:{package.name}")],
        )

    def test_07_partial_payments_grant_only_on_exact_full_amount(self):
        package = self.package().submit()
        self.payment(package, 600).submit()
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 0)
        self.payment(package, 1400).submit()
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 1)
        package.reload()
        self.assertTrue(package.activated_at)

    def test_08_overpayment_is_rejected_without_grant(self):
        package = self.package().submit()
        self.payment(package, 1500).submit()
        excess = self.payment(package, 501)
        self.assertIn("超过课包成交金额", self.rejected(excess.submit))
        self.assertEqual(frappe.db.get_value("MX Payment", excess.name, "docstatus"), 0)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 0)

    def test_09_payment_api_retry_is_idempotent(self):
        from meixin_admin.payments import record_payment

        package = self.package().submit()
        paid_at = now_datetime()
        request_id = uuid.uuid4().hex
        draft = self.payment(package, 2000, request_id=request_id, paid_at=paid_at)
        first = record_payment(package.name, "2000.00", "现金", paid_at, request_id)
        second = record_payment(package.name, "2000.00", "现金", paid_at, request_id)
        self.assertEqual(first, draft.name)
        self.assertEqual(first, second)
        self.assertEqual(frappe.db.count("MX Payment", {"student_package": package.name}), 1)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 1)
        self.assertIn(
            "不同付款内容",
            self.rejected(lambda: record_payment(package.name, "1999.00", "现金", paid_at, request_id)),
        )

    def test_10_gift_grants_immediately_and_once(self):
        package = self.package("赠送").submit()
        entries = frappe.get_all(
            "MX Lesson Credit Entry", filters={"student_package": package.name},
            fields=["operation_type", "effect"],
        )
        self.assertEqual(package.deal_amount, 0)
        self.assertTrue(package.activated_at)
        self.assertEqual([(row.operation_type, row.effect) for row in entries], [("赠送授予", 20)])

    def worker(self, name, *, hold=False):
        process = subprocess.Popen(
            [sys.executable, "-m", "meixin_admin.tests.concurrent_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.workers.append(process)
        output = queue.Queue()
        threading.Thread(
            target=lambda: [output.put(line.strip()) for line in process.stdout], daemon=True,
        ).start()
        process.stdin.write(json.dumps({
            "site": frappe.local.site, "sites_path": os.path.abspath(frappe.local.sites_path),
            "operation": "payment_submit", "name": name, "hold": hold,
            "field": None, "value": None,
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

    def test_11_concurrent_partial_payments_fail_stale_request_then_grant_once_on_retry(self):
        package = self.package().submit()
        first = self.payment(package, 600)
        second = self.payment(package, 1400)
        frappe.db.commit()
        self.committed = True
        workers = (self.worker(first.name, hold=True), self.worker(second.name))
        self.read_worker(workers[0], "LOCKED")
        self.read_worker(workers[1], "READY")
        workers[1][0].stdin.write("GO\n")
        workers[1][0].stdin.flush()
        self.read_worker(workers[1], "ATTEMPT")
        time.sleep(0.5)
        self.assertIsNone(workers[1][0].poll())
        workers[0][0].stdin.write("GO\n")
        workers[0][0].stdin.flush()
        results = [
            json.loads(self.read_worker(worker, "RESULT ").removeprefix("RESULT "))
            for worker in workers
        ]
        for worker in workers:
            worker[0].wait(timeout=10)
        frappe.db.rollback()
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        self.assertIn("本次操作已回滚", results[1]["message"])
        frappe.get_doc("MX Payment", second.name).submit()
        self.assertEqual(frappe.db.count("MX Payment", {"student_package": package.name, "docstatus": 1}), 2)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 1)

    def test_12_m2_plus_one_debits_exactly_one_credit(self):
        package = self.package("赠送").submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        decision = frappe.get_doc(
            "MX Lesson Consumption Entry", {"execution": execution.name, "operation_type": "决定"}
        )
        debit = frappe.get_doc("MX Lesson Credit Entry", {"m2_consumption_entry": decision.name})
        self.assertEqual((debit.student_package, debit.effect), (package.name, -1))
        self.assertEqual(debit.idempotency_key, f"m2-consume:{decision.name}")
        self.assertEqual(entitlements.locked_credit_balance(package.name), 19)

    def test_13_m2_zero_does_not_create_credit_entry(self):
        package = self.package("赠送").submit()
        self.set_rule("不课消")
        execution = self.execution().submit()
        decision = frappe.get_doc("MX Lesson Consumption Entry", {"execution": execution.name})
        self.assertEqual(decision.effect, 0)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 1
        )
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)

    def test_14_m2_reversal_restores_original_package(self):
        package = self.package("赠送").submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        decision = frappe.get_doc(
            "MX Lesson Consumption Entry", {"execution": execution.name, "operation_type": "决定"}
        )
        debit = frappe.get_doc("MX Lesson Credit Entry", {"m2_consumption_entry": decision.name})
        execution.cancel()
        reversal = frappe.get_doc(
            "MX Lesson Consumption Entry", {"reversal_of": decision.name}
        )
        restore = frappe.get_doc("MX Lesson Credit Entry", {"m2_consumption_entry": reversal.name})
        self.assertEqual((restore.student_package, restore.effect, restore.reversal_of),
                         (package.name, 1, debit.name))
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)

    def test_15_repeated_m2_decision_does_not_debit_twice(self):
        package = self.package("赠送").submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        session = frappe.get_doc("MX Session", execution.session)
        with consumption.schedule_write():
            repeated, _ = consumption.create_decision(
                session, self.student.name, "到课", execution=execution.name,
                key_prefix="execution",
            )
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"m2_consumption_entry": repeated.name}), 1
        )
        self.assertEqual(entitlements.locked_credit_balance(package.name), 19)

    def test_16_repeated_m2_reversal_does_not_restore_twice(self):
        package = self.package("赠送").submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        decision = frappe.db.get_value(
            "MX Lesson Consumption Entry", {"execution": execution.name, "operation_type": "决定"}, "name"
        )
        first = consumption.manual_reverse(decision, "M3 幂等返还测试")
        second = consumption.manual_reverse(decision, "M3 幂等返还测试")
        self.assertEqual(first, second)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"m2_consumption_entry": first}), 1
        )
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)

    def test_17_no_eligible_package_rolls_back_execution_and_m2(self):
        self.set_rule("课消")
        execution = self.execution()
        self.assertIn("没有可扣减", self.rejected(execution.submit))
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 0)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"operation_type": "M2 课消扣减"}), 0
        )

    def test_18_credit_unique_failure_rolls_back_execution_and_m2(self):
        package = self.package("赠送").submit()
        self.set_rule("课消")
        execution = self.execution()
        with patch(
            "meixin_admin.entitlements.insert_credit_idempotent",
            side_effect=frappe.UniqueValidationError("测试注入权益唯一键冲突"),
        ):
            self.rejected(execution.submit, frappe.UniqueValidationError)
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 0)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 1
        )

    def test_19_restore_failure_rolls_back_m2_reversal_and_execution_cancel(self):
        package = self.package("赠送").submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        decision = frappe.db.get_value(
            "MX Lesson Consumption Entry", {"execution": execution.name, "operation_type": "决定"}, "name"
        )
        with patch(
            "meixin_admin.entitlements.restore_m2_entry",
            side_effect=frappe.ValidationError("测试注入权益返还失败"),
        ):
            self.rejected(execution.cancel)
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 1)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"reversal_of": decision}), 0)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 2
        )
        self.assertEqual(entitlements.locked_credit_balance(package.name), 19)

    def test_20_fresh_retry_after_credit_failure_creates_one_chain(self):
        package = self.package("赠送").submit()
        self.set_rule("课消")
        execution = self.execution()
        with patch(
            "meixin_admin.entitlements.consume_m2_entry",
            side_effect=frappe.ValidationError("测试注入扣减前失败"),
        ):
            self.rejected(execution.submit)
        execution.reload().submit()
        decisions = frappe.get_all(
            "MX Lesson Consumption Entry",
            filters={"execution": execution.name, "operation_type": "决定"}, pluck="name",
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"m2_consumption_entry": decisions[0]}), 1
        )
        self.assertEqual(entitlements.locked_credit_balance(package.name), 19)


if __name__ == "__main__":
    unittest.main()
