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
from frappe.utils import add_days, now_datetime, nowdate

from meixin_admin import consumption
from meixin_admin import entitlements
from meixin_admin import payments
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
            for field in ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule"):
                frappe.db.set_single_value("MX Settings", field, "未配置")
            frappe.db.commit()

    def new_plan(self, *, course=None, credits=20, price=2000):
        return frappe.get_doc({
            "doctype": "MX Package Plan", "plan_name": "M3测试课包-" + uuid.uuid4().hex[:8],
            "course": (course or self.course).name, "standard_credits": credits,
            "standard_price": price, "currency": "CNY", "enabled": 1,
            "demo_batch": self.batch,
        }).insert()

    def package(self, acquisition_type="购买", *, plan=None, effective_from=None, expires_on=None):
        return frappe.get_doc({
            "doctype": "MX Student Package", "student": self.student.name,
            "package_plan": (plan or self.plan).name, "acquisition_type": acquisition_type,
            "effective_from": effective_from or nowdate(), "expires_on": expires_on,
            "demo_batch": self.batch,
        }).insert()

    def user(self, role=None, user_type="System User"):
        return frappe.get_doc({
            "doctype": "User", "email": f"mx-m3-{uuid.uuid4().hex[:16]}@example.invalid",
            "first_name": "M3隔离用户", "enabled": 1, "send_welcome_email": 0,
            "user_type": user_type, "roles": [{"role": role}] if role else [],
        }).insert().name

    def payment(self, package, amount, *, request_id=None, paid_at=None):
        return frappe.get_doc({
            "doctype": "MX Payment", "student_package": package.name,
            "operation_type": "收款", "amount": amount, "currency": "CNY",
            "payment_method": "现金", "paid_at": paid_at or now_datetime(),
            "request_id": request_id, "demo_batch": self.batch,
        }).insert()

    def session(self, *, course=None, start=None):
        start = start or now_datetime() + timedelta(hours=1)
        return frappe.get_doc({
            "doctype": "MX Session", "course": (course or self.course).name,
            "teacher": self.teacher.name, "room": self.room.name,
            "start_at": start, "end_at": start + timedelta(hours=1),
            "students": [{"student": self.student.name}], "demo_batch": self.batch,
        }).insert().submit()

    def execution(self, status="到课", *, session=None):
        session = session or self.session()
        return frappe.get_doc({
            "doctype": "MX Session Execution", "session": session.name,
            "attendance": [{"student": self.student.name, "attendance_status": status}],
            "early_completion_reason": "M3 隔离测试提前完成",
        }).insert()

    def set_rule(self, value):
        settings = frappe.get_single("MX Settings")
        settings.present_rule = value
        settings.save()

    def purchase_and_activate(self, *, plan=None, effective_from=None, expires_on=None):
        package = self.package(
            plan=plan, effective_from=effective_from, expires_on=expires_on,
        ).submit()
        self.payment(package, package.deal_amount).submit()
        package.reload()
        return package

    def preset_submitted_payment(self, package, operation_type, amount, *, reversal_of=None):
        payment = frappe.get_doc({
            "doctype": "MX Payment", "student_package": package.name,
            "operation_type": operation_type, "amount": amount, "currency": "CNY",
            "payment_method": "现金", "paid_at": now_datetime(),
            "reversal_of": reversal_of, "reason": "仅用于 4C 状态候选测试",
            "demo_batch": self.batch,
        }).insert()
        frappe.db.set_value("MX Payment", payment.name, "docstatus", 1, update_modified=False)
        payment.docstatus = 1
        return payment

    def correction_payment(self, package, operation_type, amount, *, reversal_of=None, request_id=None):
        return frappe.get_doc({
            "doctype": "MX Payment", "student_package": package.name,
            "operation_type": operation_type, "amount": amount, "currency": "CNY",
            "payment_method": "其他", "paid_at": now_datetime(),
            "reversal_of": reversal_of, "reason": "M3 4D 隔离测试",
            "request_id": request_id, "demo_batch": self.batch,
        }).insert()

    def refund_close(self, package, amount=None, *, request_id=None):
        name = payments.refund_close_package(
            package.name, amount or package.deal_amount, "M3 4D 退款关闭测试",
            request_id or uuid.uuid4().hex,
        )
        return frappe.get_doc("MX Payment", name)

    def reverse(self, payment, *, request_id=None):
        name = payments.reverse_payment(
            payment.name, "M3 4D 撤销测试", request_id or uuid.uuid4().hex,
        )
        return frappe.get_doc("MX Payment", name)

    def debited_package(self, execution):
        decision = frappe.db.get_value(
            "MX Lesson Consumption Entry",
            {"execution": execution.name, "operation_type": "决定"}, "name",
        )
        return frappe.db.get_value(
            "MX Lesson Credit Entry", {"m2_consumption_entry": decision}, "student_package",
        )

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

    def worker(self, name, *, operation="payment_submit", hold=False, field=None, value=None):
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
            "operation": operation, "name": name, "hold": hold,
            "field": field, "value": value,
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
        results = [
            json.loads(self.read_worker(worker, "RESULT ").removeprefix("RESULT "))
            for worker in (first, second)
        ]
        for worker in (first, second):
            worker[0].wait(timeout=10)
        frappe.db.rollback()
        return results

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
        self.assertIn("没有购买", self.rejected(execution.submit))
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

    def test_21_fefo_selects_earliest_expiry(self):
        later = self.package("赠送", expires_on=add_days(nowdate(), 20)).submit()
        earlier = self.package("赠送", expires_on=add_days(nowdate(), 10)).submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        self.assertEqual(self.debited_package(execution), earlier.name)
        self.assertEqual(entitlements.locked_credit_balance(later.name), 20)

    def test_22_fifo_selects_earliest_activation_for_same_expiry(self):
        expiry = add_days(nowdate(), 10)
        earlier = self.package("赠送", expires_on=expiry).submit()
        later = self.package("赠送", expires_on=expiry).submit()
        frappe.db.set_value("MX Student Package", earlier.name, "activated_at", "2026-01-01 09:00:00")
        frappe.db.set_value("MX Student Package", later.name, "activated_at", "2026-01-02 09:00:00")
        self.set_rule("课消")
        execution = self.execution().submit()
        self.assertEqual(self.debited_package(execution), earlier.name)

    def test_23_name_is_stable_final_tie_breaker(self):
        expiry = add_days(nowdate(), 10)
        packages = [self.package("赠送", expires_on=expiry).submit() for _ in range(2)]
        for package in packages:
            frappe.db.set_value("MX Student Package", package.name, "activated_at", "2026-01-01 09:00:00")
        self.set_rule("课消")
        execution = self.execution().submit()
        self.assertEqual(self.debited_package(execution), min(package.name for package in packages))

    def test_24_package_without_expiry_sorts_last(self):
        no_expiry = self.package("赠送").submit()
        expiring = self.package("赠送", expires_on=add_days(nowdate(), 30)).submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        self.assertEqual(self.debited_package(execution), expiring.name)
        self.assertEqual(entitlements.locked_credit_balance(no_expiry.name), 20)

    def test_25_exact_course_match_ignores_other_course(self):
        other_course = frappe.get_doc({
            "doctype": "MX Course", "course_name": "M3其他课程",
            "default_duration_minutes": 60, "enabled": 1, "demo_batch": self.batch,
        }).insert()
        other = self.package(
            "赠送", plan=self.new_plan(course=other_course),
            expires_on=add_days(nowdate(), 1),
        ).submit()
        correct = self.package("赠送", expires_on=add_days(nowdate(), 20)).submit()
        self.set_rule("课消")
        execution = self.execution().submit()
        self.assertEqual(self.debited_package(execution), correct.name)
        self.assertEqual(entitlements.locked_credit_balance(other.name), 20)

    def test_26_different_course_never_debits_and_reports_no_package(self):
        other_course = frappe.get_doc({
            "doctype": "MX Course", "course_name": "M3不匹配课程",
            "default_duration_minutes": 60, "enabled": 1, "demo_batch": self.batch,
        }).insert()
        other = self.package("赠送", plan=self.new_plan(course=other_course)).submit()
        self.set_rule("课消")
        execution = self.execution()
        self.assertIn("没有购买该课程", self.rejected(execution.submit))
        self.assertEqual(entitlements.locked_credit_balance(other.name), 20)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)

    def test_27_unpaid_purchase_is_not_eligible(self):
        package = self.package().submit()
        self.set_rule("课消")
        execution = self.execution()
        self.assertIn("尚未付清", self.rejected(execution.submit))
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 0)

    def test_28_exhausted_package_is_not_eligible(self):
        package = self.package("赠送", plan=self.new_plan(credits=1, price=100)).submit()
        self.set_rule("课消")
        self.execution(session=self.session(start=now_datetime() + timedelta(hours=1))).submit()
        second = self.execution(session=self.session(start=now_datetime() + timedelta(hours=3)))
        self.assertIn("已耗尽", self.rejected(second.submit))
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": second.name}), 0)

    def test_29_future_package_is_not_eligible(self):
        self.package("赠送", effective_from=add_days(nowdate(), 1)).submit()
        self.set_rule("课消")
        self.assertIn("尚未生效", self.rejected(self.execution().submit))

    def test_30_expired_package_is_not_eligible_even_with_balance(self):
        package = self.package(
            "赠送", effective_from=add_days(nowdate(), -10), expires_on=add_days(nowdate(), -1),
        ).submit()
        self.set_rule("课消")
        self.assertIn("已经过期", self.rejected(self.execution().submit))
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)

    def test_31_underpaid_after_grant_is_frozen_without_changing_credits(self):
        package = self.purchase_and_activate()
        receipt = frappe.get_doc("MX Payment", {"student_package": package.name, "operation_type": "收款"})
        self.preset_submitted_payment(package, "撤销", receipt.amount, reversal_of=receipt.name)
        self.set_rule("课消")
        execution = self.execution()
        self.assertIn("欠费冻结", self.rejected(execution.submit))
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"student_package": package.name}), 1)
        self.payment(package, package.deal_amount).submit()
        execution.reload().submit()
        self.assertEqual(self.debited_package(execution), package.name)
        self.assertEqual(
            frappe.db.count(
                "MX Lesson Credit Entry",
                {"student_package": package.name, "operation_type": "购买授予"},
            ),
            1,
        )

    def test_32_refund_closed_package_is_not_eligible(self):
        package = self.purchase_and_activate()
        self.preset_submitted_payment(package, "退款关闭课包", package.deal_amount)
        self.set_rule("课消")
        self.assertIn("退款关闭", self.rejected(self.execution().submit))
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)

    def test_33_effective_and_expiry_dates_are_both_inclusive(self):
        package = self.package(
            "赠送", effective_from=nowdate(), expires_on=nowdate(),
        ).submit()
        self.set_rule("课消")
        execution = self.execution(session=self.session(start=now_datetime())).submit()
        self.assertEqual(self.debited_package(execution), package.name)

    def test_34_exhausted_first_package_falls_through_to_second(self):
        first = self.package(
            "赠送", plan=self.new_plan(credits=1, price=100),
            expires_on=add_days(nowdate(), 5),
        ).submit()
        second = self.package(
            "赠送", plan=self.new_plan(credits=1, price=100),
            expires_on=add_days(nowdate(), 10),
        ).submit()
        self.set_rule("课消")
        first_execution = self.execution(
            session=self.session(start=now_datetime() + timedelta(hours=1))
        ).submit()
        second_execution = self.execution(
            session=self.session(start=now_datetime() + timedelta(hours=3))
        ).submit()
        self.assertEqual(self.debited_package(first_execution), first.name)
        self.assertEqual(self.debited_package(second_execution), second.name)
        self.assertEqual(entitlements.locked_credit_balance(first.name), 0)
        self.assertEqual(entitlements.locked_credit_balance(second.name), 0)

    def test_35_concurrent_last_credit_allows_only_one_consumption(self):
        package = self.package("赠送", plan=self.new_plan(credits=1, price=100)).submit()
        self.set_rule("课消")
        executions = [
            self.execution(session=self.session(start=now_datetime() + timedelta(hours=offset)))
            for offset in (1, 3)
        ]
        frappe.db.commit()
        self.committed = True
        workers = (
            self.worker(executions[0].name, operation="execution_submit", hold=True),
            self.worker(executions[1].name, operation="execution_submit"),
        )
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
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"operation_type": "M2 课消扣减", "demo_batch": self.batch}),
            1,
        )
        self.assertEqual(
            frappe.db.count("MX Lesson Consumption Entry", {"effect": 1, "demo_batch": self.batch}), 1
        )

    def test_36_fresh_retry_selects_second_package_after_concurrent_exhaustion(self):
        first = self.package(
            "赠送", plan=self.new_plan(credits=1, price=100),
            expires_on=add_days(nowdate(), 5),
        ).submit()
        second = self.package(
            "赠送", plan=self.new_plan(credits=1, price=100),
            expires_on=add_days(nowdate(), 10),
        ).submit()
        self.set_rule("课消")
        executions = [
            self.execution(session=self.session(start=now_datetime() + timedelta(hours=offset)))
            for offset in (1, 3)
        ]
        frappe.db.commit()
        self.committed = True
        workers = (
            self.worker(executions[0].name, operation="execution_submit", hold=True),
            self.worker(executions[1].name, operation="execution_submit"),
        )
        self.read_worker(workers[0], "LOCKED")
        self.read_worker(workers[1], "READY")
        workers[1][0].stdin.write("GO\n")
        workers[1][0].stdin.flush()
        self.read_worker(workers[1], "ATTEMPT")
        time.sleep(0.5)
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
        retried = frappe.get_doc("MX Session Execution", executions[1].name).submit()
        self.assertEqual(self.debited_package(retried), second.name)
        self.assertEqual(entitlements.locked_credit_balance(first.name), 0)
        self.assertEqual(entitlements.locked_credit_balance(second.name), 0)

    def test_37_grant_without_activation_metadata_is_rejected(self):
        package = self.package("赠送").submit()
        frappe.db.set_value("MX Student Package", package.name, "activated_at", None)
        self.set_rule("课消")
        execution = self.execution()
        self.assertIn("初始权益", self.rejected(execution.submit))
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)

    def test_38_duplicate_initial_grant_history_is_rejected(self):
        package = self.package("赠送").submit()
        values = {
            "doctype": "MX Lesson Credit Entry",
            "student": package.student,
            "student_name_snapshot": self.student.student_name,
            "student_package": package.name,
            "package_plan": package.package_plan,
            "plan_name_snapshot": package.plan_name_snapshot,
            "course": package.course,
            "course_name_snapshot": package.course_name_snapshot,
            "operation_type": "赠送授予",
            "effect": package.credits_granted,
            "source_doctype": "MX Student Package",
            "source_name": package.name,
            "idempotency_key": "test-duplicate-grant:" + uuid.uuid4().hex,
            "demo_batch": self.batch,
        }
        entitlements.insert_credit_idempotent(values)
        self.set_rule("课消")
        execution = self.execution()
        self.assertIn("初始权益", self.rejected(execution.submit))
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)

    def test_39_invalid_validity_metadata_is_rejected(self):
        package = self.package("赠送").submit()
        frappe.db.set_value("MX Student Package", package.name, "effective_from", None)
        self.set_rule("课消")
        execution = self.execution()
        self.assertIn("元数据异常", self.rejected(execution.submit))
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 20)

    def test_40_receipt_reversal_freezes_then_supplement_restores_without_new_grant(self):
        package = self.purchase_and_activate()
        receipt = frappe.get_doc("MX Payment", {"student_package": package.name, "operation_type": "收款"})
        self.set_rule("课消")
        self.execution(session=self.session(start=now_datetime() + timedelta(hours=1))).submit()
        reversal = self.reverse(receipt)
        self.assertEqual(reversal.cash_effect, -receipt.cash_effect)
        self.assertEqual(reversal.reversal_of, receipt.name)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 19)
        blocked = self.execution(session=self.session(start=now_datetime() + timedelta(hours=3)))
        self.assertIn("欠费冻结", self.rejected(blocked.submit))
        self.payment(package, package.deal_amount).submit()
        blocked.reload().submit()
        self.assertEqual(entitlements.locked_credit_balance(package.name), 18)
        self.assertEqual(
            frappe.db.count(
                "MX Lesson Credit Entry",
                {"student_package": package.name, "operation_type": "购买授予"},
            ),
            1,
        )
        receipt.reload()
        self.assertEqual(receipt.cash_effect, package.deal_amount)
        receipt.amount = 1
        self.rejected(receipt.save)
        self.rejected(receipt.cancel)
        self.rejected(lambda: frappe.delete_doc("MX Payment", receipt.name))

    def test_41_payment_reversal_is_idempotent_and_unique(self):
        package = self.purchase_and_activate()
        receipt = frappe.get_doc("MX Payment", {"student_package": package.name, "operation_type": "收款"})
        request_id = uuid.uuid4().hex
        first = payments.reverse_payment(receipt.name, "重复撤销测试", request_id)
        second = payments.reverse_payment(receipt.name, "重复撤销测试", request_id)
        self.assertEqual(first, second)
        self.assertIn(
            "撤销",
            self.rejected(lambda: payments.reverse_payment(
                receipt.name, "第二条撤销", uuid.uuid4().hex,
            )),
        )
        self.assertEqual(frappe.db.count("MX Payment", {"reversal_of": receipt.name}), 1)

    def test_42_correction_apis_require_manager(self):
        package = self.purchase_and_activate()
        receipt = frappe.get_doc("MX Payment", {"student_package": package.name, "operation_type": "收款"})
        for user in (
            self.user("Meixin Scheduler"), self.user("System Manager"), self.user(),
            self.user(user_type="Website User"), "Guest",
        ):
            frappe.set_user(user)
            self.rejected(
                lambda: payments.reverse_payment(receipt.name, "越权撤销", uuid.uuid4().hex),
                frappe.PermissionError,
            )
            self.rejected(
                lambda: payments.refund_close_package(
                    package.name, 100, "越权退款", uuid.uuid4().hex,
                ),
                frappe.PermissionError,
            )
            self.rejected(
                lambda: entitlements.adjust_credits(
                    package.name, 1, "越权调整", uuid.uuid4().hex,
                ),
                frappe.PermissionError,
            )
        frappe.set_user("Administrator")
        scheduler = self.user("Meixin Scheduler")
        frappe.set_user(scheduler)
        self.rejected(
            lambda: self.correction_payment(package, "退款关闭课包", 100),
            frappe.PermissionError,
        )

    def test_43_refund_close_reclaims_all_remaining_credit(self):
        package = self.purchase_and_activate()
        self.set_rule("课消")
        self.execution().submit()
        refund = self.refund_close(package, 1000)
        reclaim = frappe.get_doc(
            "MX Lesson Credit Entry",
            {"source_doctype": "MX Payment", "source_name": refund.name, "operation_type": "退款收回"},
        )
        self.assertEqual((refund.cash_effect, refund.credits_reclaimed), (-1000, 19))
        self.assertEqual((reclaim.effect, reclaim.student_package), (-19, package.name))
        self.assertEqual(reclaim.idempotency_key, f"payment-refund-reclaim:{refund.name}")
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)
        self.assertEqual(
            frappe.db.count(
                "MX Lesson Credit Entry", {"student_package": package.name, "operation_type": "M2 课消扣减"}
            ),
            1,
        )

    def test_44_refund_amount_bounds_are_atomic(self):
        package = self.purchase_and_activate()
        before = frappe.db.count("MX Payment", {"student_package": package.name})
        for amount in (0, -1, 2001, "NaN", "Infinity"):
            self.rejected(lambda amount=amount: payments.refund_close_package(
                package.name, amount, "非法退款金额", uuid.uuid4().hex,
            ))
        self.assertEqual(frappe.db.count("MX Payment", {"student_package": package.name}), before)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款收回"}), 0)

    def test_45_exhausted_package_can_refund_close_without_zero_credit_entry(self):
        plan = self.new_plan(credits=1, price=100)
        package = self.purchase_and_activate(plan=plan)
        self.set_rule("课消")
        self.execution().submit()
        refund = self.refund_close(package, 100)
        self.assertEqual(refund.credits_reclaimed, 0)
        self.assertEqual(
            frappe.db.count(
                "MX Lesson Credit Entry", {"source_doctype": "MX Payment", "source_name": refund.name}
            ),
            0,
        )
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)

    def test_46_refund_close_blocks_repeat_receipt_and_consumption(self):
        package = self.purchase_and_activate()
        self.refund_close(package, 1000)
        self.assertIn("重复退款", self.rejected(lambda: self.refund_close(package, 100)))
        self.assertIn("退款关闭", self.rejected(lambda: self.payment(package, 100).submit()))
        self.set_rule("课消")
        self.assertIn("退款关闭", self.rejected(self.execution().submit))

    def test_47_refund_reversal_restores_exact_reclaimed_snapshot(self):
        package = self.purchase_and_activate()
        self.set_rule("课消")
        self.execution(session=self.session(start=now_datetime() + timedelta(hours=1))).submit()
        refund = self.refund_close(package, 1000)
        reclaim = frappe.get_doc(
            "MX Lesson Credit Entry", {"source_name": refund.name, "operation_type": "退款收回"}
        )
        reversal = self.reverse(refund)
        restore = frappe.get_doc(
            "MX Lesson Credit Entry", {"source_name": reversal.name, "operation_type": "退款 reversal 恢复"}
        )
        self.assertEqual(reversal.cash_effect, 1000)
        self.assertEqual((restore.effect, restore.reversal_of), (19, reclaim.name))
        self.assertEqual(entitlements.locked_credit_balance(package.name), 19)
        self.execution(session=self.session(start=now_datetime() + timedelta(hours=3))).submit()
        self.assertEqual(entitlements.locked_credit_balance(package.name), 18)

    def test_48_refund_reversal_is_idempotent_and_zero_snapshot_stays_zero(self):
        plan = self.new_plan(credits=1, price=100)
        package = self.purchase_and_activate(plan=plan)
        self.set_rule("课消")
        self.execution().submit()
        refund = self.refund_close(package, 100)
        request_id = uuid.uuid4().hex
        first = payments.reverse_payment(refund.name, "退款撤销幂等", request_id)
        second = payments.reverse_payment(refund.name, "退款撤销幂等", request_id)
        self.assertEqual(first, second)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款 reversal 恢复"}), 0
        )
        self.assertIn(
            "撤销",
            self.rejected(lambda: payments.reverse_payment(
                refund.name, "重复退款撤销", uuid.uuid4().hex,
            )),
        )

    def test_49_manual_positive_adjustment_is_audited_and_idempotent(self):
        package = self.package("赠送").submit()
        request_id = uuid.uuid4().hex
        first = entitlements.adjust_credits(package.name, 3, "竞赛奖励", request_id)
        second = entitlements.adjust_credits(package.name, "3", "竞赛奖励", request_id)
        self.assertEqual(first, second)
        entry = frappe.get_doc("MX Lesson Credit Entry", first)
        self.assertEqual((entry.operation_type, entry.effect, entry.reason), ("人工调整", 3, "竞赛奖励"))
        self.assertEqual(entry.owner, "Administrator")
        self.assertEqual(entry.idempotency_key, f"manual-adjustment:{request_id}")
        self.assertEqual(entitlements.locked_credit_balance(package.name), 23)

    def test_50_manual_negative_adjustment_can_reach_zero_but_not_below(self):
        package = self.package("赠送", plan=self.new_plan(credits=2, price=100)).submit()
        entitlements.adjust_credits(package.name, -2, "清零纠正", uuid.uuid4().hex)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)
        self.assertIn("小于 0", self.rejected(lambda: entitlements.adjust_credits(
            package.name, -1, "不得负数", uuid.uuid4().hex,
        )))
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"operation_type": "人工调整"}), 1
        )

    def test_51_manual_adjustment_validates_integer_reason_and_idempotent_content(self):
        package = self.package("赠送").submit()
        for effect, reason in ((0, "零"), ("1.5", "小数"), (1, "   ")):
            self.rejected(lambda effect=effect, reason=reason: entitlements.adjust_credits(
                package.name, effect, reason, uuid.uuid4().hex,
            ))
        request_id = uuid.uuid4().hex
        entitlements.adjust_credits(package.name, 1, "原内容", request_id)
        self.assertIn("不同调整内容", self.rejected(lambda: entitlements.adjust_credits(
            package.name, 2, "不同内容", request_id,
        )))

    def test_52_refund_closed_package_rejects_manual_adjustment(self):
        package = self.purchase_and_activate()
        self.refund_close(package, 1000)
        self.assertIn("已退款关闭", self.rejected(lambda: entitlements.adjust_credits(
            package.name, 1, "试图恢复", uuid.uuid4().hex,
        )))

    def test_53_correction_apis_respect_user_permission_and_reject_docshare(self):
        package = self.purchase_and_activate()
        receipt = frappe.get_doc("MX Payment", {"student_package": package.name, "operation_type": "收款"})
        manager = self.user("Meixin Manager")
        other_student = frappe.get_doc({
            "doctype": "MX Student", "student_name": "M3虚构其他学生",
            "guardian_phone": "00000000001", "enabled": 1, "demo_batch": self.batch,
        }).insert()
        frappe.get_doc({
            "doctype": "User Permission", "user": manager, "allow": "MX Student",
            "for_value": other_student.name, "apply_to_all_doctypes": 1,
        }).insert(ignore_permissions=True)
        frappe.set_user(manager)
        self.rejected(
            lambda: payments.reverse_payment(receipt.name, "受限用户撤销", uuid.uuid4().hex),
            frappe.PermissionError,
        )
        self.rejected(
            lambda: payments.refund_close_package(
                package.name, 100, "受限用户退款", uuid.uuid4().hex,
            ),
            frappe.PermissionError,
        )
        self.rejected(
            lambda: entitlements.adjust_credits(
                package.name, 1, "受限用户调整", uuid.uuid4().hex,
            ),
            frappe.PermissionError,
        )
        frappe.set_user("Administrator")
        outsider = self.user("System Manager")
        self.rejected(lambda: frappe.get_doc({
            "doctype": "DocShare", "share_doctype": "MX Student Package",
            "share_name": package.name, "user": outsider, "read": 1,
        }).insert(), frappe.PermissionError)

    def test_54_payment_reversal_failure_rolls_back_then_retry_succeeds_once(self):
        package = self.purchase_and_activate()
        receipt = frappe.get_doc("MX Payment", {"student_package": package.name, "operation_type": "收款"})
        request_id = uuid.uuid4().hex
        real_finalize = payments._finalize_reversal

        def fail_after_finalize(payment, locked_package, state):
            real_finalize(payment, locked_package, state)
            raise frappe.ValidationError("注入撤销后续失败")

        with patch("meixin_admin.payments._finalize_reversal", side_effect=fail_after_finalize):
            self.rejected(lambda: payments.reverse_payment(
                receipt.name, "故障撤销", request_id,
            ))
        self.assertEqual(frappe.db.count("MX Payment", {"reversal_of": receipt.name}), 0)
        payments.reverse_payment(receipt.name, "故障撤销", request_id)
        self.assertEqual(frappe.db.count("MX Payment", {"reversal_of": receipt.name}), 1)

    def test_55_refund_failures_rollback_payment_and_reclaim_then_retry_once(self):
        package = self.purchase_and_activate()
        request_id = uuid.uuid4().hex
        with patch(
            "meixin_admin.entitlements.reclaim_refund_credits",
            side_effect=frappe.ValidationError("注入收回前失败"),
        ):
            self.rejected(lambda: payments.refund_close_package(
                package.name, 1000, "故障退款", request_id,
            ))
        self.assertEqual(frappe.db.count("MX Payment", {"operation_type": "退款关闭课包"}), 0)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款收回"}), 0)

        real_reclaim = entitlements.reclaim_refund_credits

        def fail_after_reclaim(locked_package, payment):
            real_reclaim(locked_package, payment)
            raise frappe.ValidationError("注入收回后失败")

        with patch("meixin_admin.entitlements.reclaim_refund_credits", side_effect=fail_after_reclaim):
            self.rejected(lambda: payments.refund_close_package(
                package.name, 1000, "故障退款", request_id,
            ))
        self.assertEqual(frappe.db.count("MX Payment", {"operation_type": "退款关闭课包"}), 0)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款收回"}), 0)
        refund_name = payments.refund_close_package(package.name, 1000, "故障退款", request_id)
        self.assertEqual(frappe.db.count("MX Payment", {"name": refund_name}), 1)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款收回"}), 1)

    def test_56_refund_reversal_restore_failure_rolls_back_then_retry_once(self):
        package = self.purchase_and_activate()
        refund = self.refund_close(package, 1000)
        request_id = uuid.uuid4().hex
        with patch(
            "meixin_admin.entitlements.restore_refund_credits",
            side_effect=frappe.ValidationError("注入退款恢复失败"),
        ):
            self.rejected(lambda: payments.reverse_payment(
                refund.name, "故障退款撤销", request_id,
            ))
        self.assertEqual(frappe.db.count("MX Payment", {"reversal_of": refund.name}), 0)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款 reversal 恢复"}), 0,
        )
        payments.reverse_payment(refund.name, "故障退款撤销", request_id)
        self.assertEqual(frappe.db.count("MX Payment", {"reversal_of": refund.name}), 1)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款 reversal 恢复"}), 1,
        )

    def test_57_manual_adjustment_insert_failure_rolls_back_then_retry_once(self):
        package = self.package("赠送").submit()
        request_id = uuid.uuid4().hex
        with patch(
            "meixin_admin.entitlements.insert_credit_idempotent",
            side_effect=frappe.ValidationError("注入人工调整写入失败"),
        ):
            self.rejected(lambda: entitlements.adjust_credits(
                package.name, 2, "故障后重试", request_id,
            ))
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"idempotency_key": f"manual-adjustment:{request_id}"}),
            0,
        )
        entitlements.adjust_credits(package.name, 2, "故障后重试", request_id)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"idempotency_key": f"manual-adjustment:{request_id}"}),
            1,
        )

    def test_58_concurrent_refund_wins_over_new_consumption_atomically(self):
        package = self.purchase_and_activate()
        self.set_rule("课消")
        refund = self.correction_payment(package, "退款关闭课包", 1000)
        execution = self.execution()
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker(refund.name, hold=True),
            self.worker(execution.name, operation="execution_submit"),
        )
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 0)
        self.assertEqual(frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款收回"}), 1)

    def test_59_concurrent_reversal_and_supplement_keep_net_and_single_grant(self):
        package = self.purchase_and_activate()
        receipt = frappe.get_doc("MX Payment", {"student_package": package.name, "operation_type": "收款"})
        reversal = self.correction_payment(
            package, "撤销", receipt.amount, reversal_of=receipt.name,
        )
        supplement = self.payment(package, package.deal_amount)
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker(reversal.name, hold=True), self.worker(supplement.name),
        )
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        frappe.get_doc("MX Payment", supplement.name).submit()
        self.assertEqual(payments.locked_net_paid(package.name), package.deal_amount)
        self.assertEqual(
            frappe.db.count(
                "MX Lesson Credit Entry",
                {"student_package": package.name, "operation_type": "购买授予"},
            ),
            1,
        )

    def test_60_concurrent_double_refund_allows_only_one_close(self):
        package = self.purchase_and_activate()
        refunds = [self.correction_payment(package, "退款关闭课包", 1000) for _ in range(2)]
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker(refunds[0].name, hold=True), self.worker(refunds[1].name),
        )
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        self.assertEqual(
            frappe.db.count(
                "MX Payment", {"student_package": package.name, "operation_type": "退款关闭课包", "docstatus": 1},
            ),
            1,
        )
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)

    def test_61_concurrent_negative_adjustments_cannot_overdraw(self):
        package = self.package("赠送", plan=self.new_plan(credits=1, price=100)).submit()
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker(
                package.name, operation="credit_adjust", hold=True,
                field=-1, value=uuid.uuid4().hex,
            ),
            self.worker(
                package.name, operation="credit_adjust",
                field=-1, value=uuid.uuid4().hex,
            ),
        )
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        self.assertEqual(entitlements.locked_credit_balance(package.name), 0)
        self.assertEqual(
            frappe.db.count(
                "MX Lesson Credit Entry",
                {"student_package": package.name, "operation_type": "人工调整"},
            ),
            1,
        )

    def test_62_concurrent_refund_reversal_then_consumption_is_one_legal_order(self):
        package = self.purchase_and_activate()
        refund = self.refund_close(package, 1000)
        reversal = self.correction_payment(
            package, "撤销", refund.amount, reversal_of=refund.name,
        )
        self.set_rule("课消")
        execution = self.execution()
        frappe.db.commit()
        self.committed = True
        results = self.concurrent_pair(
            self.worker(reversal.name, hold=True),
            self.worker(execution.name, operation="execution_submit"),
        )
        self.assertEqual([row["ok"] for row in results], [True, False], results)
        frappe.get_doc("MX Session Execution", execution.name).submit()
        self.assertEqual(entitlements.locked_credit_balance(package.name), 19)
        self.assertEqual(
            frappe.db.count("MX Lesson Credit Entry", {"operation_type": "退款 reversal 恢复"}), 1,
        )
        self.assertEqual(frappe.db.count("MX Lesson Consumption Entry", {"execution": execution.name}), 1)


if __name__ == "__main__":
    unittest.main()
