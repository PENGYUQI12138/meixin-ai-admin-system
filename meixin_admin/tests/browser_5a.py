"""Disposable, isolated-site records for the Stage 5A browser check."""

import uuid
from pathlib import Path

import frappe
from frappe.utils import now_datetime, nowdate
from frappe.utils.password import update_password

from meixin_admin.tests.run import assert_isolated_site


BATCH = "TEST-M3-5A-BROWSER"


def inspect():
    assert_isolated_site()
    records = {
        doctype: frappe.get_all(doctype, filters={"demo_batch": BATCH}, pluck="name")
        for doctype in ("MX Package Plan", "MX Student Package", "MX Payment",
                        "MX Lesson Credit Entry", "MX Student", "MX Course")
    }
    records["test_users"] = frappe.get_all(
        "User", filters={"name": ["in", ["m3-5a-scheduler@example.invalid",
                                            "m3-5a-restricted@example.invalid"]]}, pluck="name",
    )
    records["test_user_roles"] = frappe.get_all(
        "Has Role", filters={"parent": ["in", ["m3-5a-scheduler@example.invalid",
                                                "m3-5a-restricted@example.invalid"]]}, pluck="name",
    )
    return records


def seed():
    assert_isolated_site()
    frappe.set_user("Administrator")
    if frappe.db.exists("MX Student", {"demo_batch": BATCH}):
        frappe.throw("5A 浏览器测试批次已有数据，须先核对清理。")
    student = frappe.get_doc({
        "doctype": "MX Student", "student_name": "5A虚构学生",
        "guardian_phone": "00000000000", "enabled": 1, "demo_batch": BATCH,
    }).insert()
    course = frappe.get_doc({
        "doctype": "MX Course", "course_name": "5A虚构课程",
        "default_duration_minutes": 60, "enabled": 1, "demo_batch": BATCH,
    }).insert()
    plan = frappe.get_doc({
        "doctype": "MX Package Plan", "plan_name": "5A虚构课包",
        "course": course.name, "standard_credits": 20,
        "standard_price": "1.23", "currency": "CNY", "enabled": 1,
        "demo_batch": BATCH,
    }).insert()
    package = frappe.get_doc({
        "doctype": "MX Student Package", "student": student.name,
        "package_plan": plan.name, "acquisition_type": "购买",
        "effective_from": nowdate(), "request_id": uuid.uuid4().hex,
        "demo_batch": BATCH,
    }).insert().submit()
    payment = frappe.get_doc({
        "doctype": "MX Payment", "student_package": package.name,
        "operation_type": "收款", "amount": "1.23", "currency": "CNY",
        "payment_method": "现金", "paid_at": now_datetime(),
        "request_id": uuid.uuid4().hex, "demo_batch": BATCH,
    }).insert().submit()
    hidden_student = frappe.get_doc({
        "doctype": "MX Student", "student_name": "5A权限参照学生",
        "guardian_phone": "00000000001", "enabled": 1, "demo_batch": BATCH,
    }).insert()
    password = Path("/run/secrets/admin_password").read_text().strip()
    for email in ("m3-5a-scheduler@example.invalid", "m3-5a-restricted@example.invalid"):
        frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "5A隔离验收",
            "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": "Meixin Scheduler"}],
        }).insert()
        update_password(email, password)
    frappe.get_doc({
        "doctype": "User Permission", "user": "m3-5a-restricted@example.invalid",
        "allow": "MX Student", "for_value": hidden_student.name,
        "apply_to_all_doctypes": 1,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    credit = frappe.db.get_value("MX Lesson Credit Entry", {
        "student_package": package.name, "operation_type": "购买授予",
    }, "name")
    return {"batch": BATCH, "plan": plan.name, "package": package.name,
            "payment": payment.name, "credit": credit, "student": student.name}


def cleanup():
    assert_isolated_site()
    frappe.set_user("Administrator")
    if frappe.db.count("MX Student", {"demo_batch": BATCH}) > 2:
        frappe.throw("5A 浏览器批次数据超出预期，拒绝自动清理。")
    users = ("m3-5a-scheduler@example.invalid", "m3-5a-restricted@example.invalid")
    frappe.db.delete("User Permission", {"user": ["in", users]})
    frappe.db.delete("Has Role", {"parent": ["in", users]})
    frappe.db.delete("User", {"name": ["in", users]})
    doctypes = ("MX Lesson Credit Entry", "MX Payment", "MX Student Package",
                "MX Package Plan", "MX Student", "MX Course")
    result = {}
    for doctype in doctypes:
        names = frappe.get_all(doctype, filters={"demo_batch": BATCH}, pluck="name")
        result[doctype] = len(names)
        if names:
            frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
            frappe.db.delete(doctype, {"demo_batch": BATCH})
    frappe.db.commit()
    return result
