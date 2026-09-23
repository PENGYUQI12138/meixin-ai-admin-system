"""Isolated-only fixtures for the M3 5B purchase/receipt browser flow."""

from pathlib import Path

import frappe
from frappe.utils.password import update_password

from meixin_admin.tests.run import assert_isolated_site


BATCH = "TEST-M3-5B-BROWSER"
USERS = ("m3-5b-scheduler@example.invalid", "m3-5b-manager@example.invalid")


def inspect():
    assert_isolated_site()
    plans = frappe.get_all("MX Package Plan", filters={"demo_batch": BATCH}, pluck="name")
    packages = frappe.get_all("MX Student Package", filters={"package_plan": ["in", plans]},
                              pluck="name") if plans else []
    payments = frappe.get_all("MX Payment", filters={"student_package": ["in", packages]},
                              pluck="name") if packages else []
    credits = frappe.get_all("MX Lesson Credit Entry", filters={"student_package": ["in", packages]},
                             pluck="name") if packages else []
    return {"plans": plans, "packages": packages, "payments": payments, "credits": credits,
            "students": frappe.get_all("MX Student", filters={"demo_batch": BATCH}, pluck="name"),
            "courses": frappe.get_all("MX Course", filters={"demo_batch": BATCH}, pluck="name"),
            "users": frappe.get_all("User", filters={"name": ["in", USERS]}, pluck="name"),
            "user_roles": frappe.get_all("Has Role", filters={"parent": ["in", USERS]}, pluck="name")}


def seed():
    assert_isolated_site()
    frappe.set_user("Administrator")
    if any(inspect().values()):
        frappe.throw("5B 浏览器批次已有数据，请先检查并清理。")
    student = frappe.get_doc({
        "doctype": "MX Student", "student_name": "5B虚构学生",
        "guardian_phone": "00000000000", "enabled": 1, "demo_batch": BATCH,
    }).insert()
    course = frappe.get_doc({
        "doctype": "MX Course", "course_name": "5B虚构课程",
        "default_duration_minutes": 60, "enabled": 1, "demo_batch": BATCH,
    }).insert()
    plan = frappe.get_doc({
        "doctype": "MX Package Plan", "plan_name": "5B虚构课包",
        "course": course.name, "standard_credits": 20,
        "standard_price": "1.23", "currency": "CNY", "enabled": 1,
        "demo_batch": BATCH,
    }).insert()
    password = Path("/run/secrets/admin_password").read_text().strip()
    for email, role in zip(USERS, ("Meixin Scheduler", "Meixin Manager")):
        frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "5B隔离验收",
            "enabled": 1, "send_welcome_email": 0, "user_type": "System User",
            "roles": [{"role": role}],
        }).insert()
        update_password(email, password)
    frappe.db.commit()
    return {"batch": BATCH, "student": student.name, "course": course.name,
            "plan": plan.name, "scheduler": USERS[0], "manager": USERS[1]}


def cleanup():
    assert_isolated_site()
    frappe.set_user("Administrator")
    records = inspect()
    if len(records["plans"]) > 1 or len(records["students"]) > 1 or len(records["courses"]) > 1:
        frappe.throw("5B 测试批次超出预期，拒绝自动清理。")
    for doctype, names in (("MX Lesson Credit Entry", records["credits"]),
                           ("MX Payment", records["payments"]),
                           ("MX Student Package", records["packages"]),
                           ("MX Package Plan", records["plans"]),
                           ("MX Student", records["students"]),
                           ("MX Course", records["courses"])):
        if names:
            frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
            frappe.db.delete(doctype, {"name": ["in", names]})
    frappe.db.delete("User Permission", {"user": ["in", USERS]})
    frappe.db.delete("Has Role", {"parent": ["in", USERS]})
    frappe.db.delete("User", {"name": ["in", USERS]})
    frappe.db.commit()
    return {key: len(value) for key, value in records.items()}
