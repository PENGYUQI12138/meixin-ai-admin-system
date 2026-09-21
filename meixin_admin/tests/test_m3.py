"""M3 stage-3 schema, snapshot and permission-boundary smoke tests."""
import unittest
import uuid

import frappe
from frappe.utils import now_datetime, nowdate

from meixin_admin.tests.run import assert_isolated_site


class TestM3Schema(unittest.TestCase):
    def setUp(self):
        assert_isolated_site()
        frappe.db.rollback()
        frappe.set_user("Administrator")
        self.batch = "TEST-M3-" + uuid.uuid4().hex[:16]
        self.student = frappe.get_doc({
            "doctype": "MX Student", "student_name": "M3虚构学生",
            "guardian_phone": "00000000000", "enabled": 1, "demo_batch": self.batch,
        }).insert()
        self.course = frappe.get_doc({
            "doctype": "MX Course", "course_name": "M3虚构课程",
            "default_duration_minutes": 60, "enabled": 1, "demo_batch": self.batch,
        }).insert()
        self.plan = frappe.get_doc({
            "doctype": "MX Package Plan", "plan_name": "M3虚构20课时包",
            "course": self.course.name, "standard_credits": 20,
            "standard_price": 2000, "currency": "CNY", "enabled": 1,
            "demo_batch": self.batch,
        }).insert()

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()

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


if __name__ == "__main__":
    unittest.main()
