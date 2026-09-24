"""Disposable M3 5D browser accounts and cleanup; isolation site only."""

from pathlib import Path

import frappe
from frappe.utils.password import update_password

from meixin_admin.tests.run import assert_isolated_site


RULES = ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule")
USERS = (
    "m3-5d-manager@example.invalid",
    "m3-5d-scheduler@example.invalid",
    "m3-5d-restricted@example.invalid",
)
LABELS = {
    "MX Student": ("student_name", ("5D虚构学生", "5D权限参照学生")),
    "MX Course": ("course_name", ("5D虚构课程",)),
    "MX Teacher": ("teacher_name", ("5D虚构教师",)),
    "MX Room": ("room_name", ("5D虚构教室",)),
    "MX Package Plan": ("plan_name", ("5D虚构课包",)),
}


def inspect():
    assert_isolated_site()
    records = {
        doctype: frappe.get_all(doctype, filters={field: ["in", labels]}, pluck="name")
        for doctype, (field, labels) in LABELS.items()
    }
    students = records["MX Student"]
    plans = records["MX Package Plan"]
    records["MX Student Package"] = (
        frappe.get_all("MX Student Package", filters={"package_plan": ["in", plans]}, pluck="name")
        if plans else []
    )
    packages = records["MX Student Package"]
    sessions = (
        frappe.get_all("MX Session Student", filters={"student": ["in", students]}, pluck="parent")
        if students else []
    )
    records["MX Session"] = sorted(set(sessions))
    records["MX Session Execution"] = (
        frappe.get_all("MX Session Execution", filters={"session": ["in", records["MX Session"]]}, pluck="name")
        if sessions else []
    )
    records["MX Payment"] = (
        frappe.get_all("MX Payment", filters={"student_package": ["in", packages]}, pluck="name")
        if packages else []
    )
    records["MX Lesson Credit Entry"] = (
        frappe.get_all("MX Lesson Credit Entry", filters={"student_package": ["in", packages]}, pluck="name")
        if packages else []
    )
    executions = records["MX Session Execution"]
    records["MX Lesson Consumption Entry"] = (
        frappe.get_all("MX Lesson Consumption Entry", filters={"execution": ["in", executions]}, pluck="name")
        if executions else []
    )
    records["users"] = frappe.get_all("User", filters={"name": ["in", USERS]}, pluck="name")
    records["user_permissions"] = frappe.get_all("User Permission", filters={"user": ["in", USERS]}, pluck="name")
    records["rules"] = {field: frappe.db.get_single_value("MX Settings", field) for field in RULES}
    return records


def seed_accounts(hidden_student):
    assert_isolated_site()
    frappe.set_user("Administrator")
    records = inspect()
    if records["users"] or len(records["MX Student"]) != 2 or hidden_student not in records["MX Student"]:
        frappe.throw("5D 隔离账号夹具状态异常。")
    password = Path("/run/secrets/admin_password").read_text().strip()
    for email, role in zip(USERS, ("Meixin Manager", "Meixin Scheduler", "Meixin Scheduler"), strict=True):
        frappe.get_doc({
            "doctype": "User", "email": email, "first_name": "5D隔离验收", "enabled": 1,
            "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": role}],
        }).insert()
        update_password(email, password)
    frappe.get_doc({
        "doctype": "User Permission", "user": USERS[2], "allow": "MX Student",
        "for_value": hidden_student, "apply_to_all_doctypes": 1,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"manager": USERS[0], "scheduler": USERS[1], "restricted": USERS[2]}


def cleanup():
    assert_isolated_site()
    frappe.set_user("Administrator")
    records = inspect()
    if len(records["MX Student"]) > 2 or len(records["MX Package Plan"]) > 1:
        frappe.throw("5D 隔离批次超出预期，拒绝自动清理。")
    for doctype in (
        "MX Lesson Credit Entry", "MX Payment", "MX Lesson Consumption Entry",
        "MX Session Execution", "MX Session", "MX Student Package", "MX Package Plan",
        "MX Student", "MX Course", "MX Teacher", "MX Room",
    ):
        names = records[doctype]
        if not names:
            continue
        if doctype == "MX Session Execution":
            frappe.db.delete("MX Session Attendance", {"parent": ["in", names]})
        if doctype == "MX Session":
            frappe.db.delete("MX Session Student", {"parent": ["in", names]})
        frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
        frappe.db.delete(doctype, {"name": ["in", names]})
    frappe.db.delete("User Permission", {"user": ["in", USERS]})
    frappe.db.delete("Has Role", {"parent": ["in", USERS]})
    frappe.db.delete("User", {"name": ["in", USERS]})
    frappe.db.commit()
    return {key: len(value) for key, value in records.items() if key != "rules"}


def restore_rules(rules):
    assert_isolated_site()
    frappe.set_user("Administrator")
    if set(rules) != set(RULES) or any(value not in {"未配置", "课消", "不课消"} for value in rules.values()):
        frappe.throw("5D 原始课消规则快照无效，拒绝恢复。")
    settings = frappe.get_single("MX Settings")
    for field in RULES:
        settings.set(field, rules[field])
    settings.save()
    frappe.db.commit()
    return {field: frappe.db.get_single_value("MX Settings", field) for field in RULES}
