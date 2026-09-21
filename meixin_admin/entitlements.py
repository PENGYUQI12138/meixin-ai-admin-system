"""M3 lesson-credit ledger primitives shared by package and M2 services."""
from contextlib import contextmanager

import frappe
from frappe.utils import cint, now_datetime

CREDIT_ENTRY_DOCTYPE = "MX Lesson Credit Entry"


@contextmanager
def credit_ledger_write():
    """Allow only an in-process business service to create immutable entries."""
    previous = getattr(frappe.flags, "mx_credit_write", False)
    frappe.flags.mx_credit_write = True
    try:
        yield
    finally:
        frappe.flags.mx_credit_write = previous


def locked_credit_balance(student_package):
    """Return the authoritative balance while the caller holds schedule_write."""
    value = frappe.db.sql(
        f"SELECT COALESCE(SUM(effect), 0) FROM `tab{CREDIT_ENTRY_DOCTYPE}` "
        "WHERE student_package=%s FOR UPDATE",
        (student_package,),
    )[0][0]
    return int(value or 0)


def _same_credit(existing, values):
    fields = (
        "student", "student_package", "package_plan", "course", "operation_type",
        "effect", "source_doctype", "source_name", "m2_consumption_entry",
        "reversal_of", "reason", "idempotency_key",
    )
    return all((existing.get(field) or "") == (values.get(field) or "") for field in fields)


def insert_credit_idempotent(values):
    existing_name = frappe.db.get_value(
        CREDIT_ENTRY_DOCTYPE, {"idempotency_key": values["idempotency_key"]}
    )
    if existing_name:
        existing = frappe.get_doc(CREDIT_ENTRY_DOCTYPE, existing_name)
        if not _same_credit(existing, values):
            frappe.throw("幂等键已被不同的课时权益内容占用，已停止写入。", title="课时权益冲突")
        return existing
    with credit_ledger_write():
        return frappe.get_doc(values).insert(ignore_permissions=True)


def grant_package(package):
    """Grant a package exactly once; caller already owns schedule_write."""
    if package.acquisition_type not in {"购买", "赠送"}:
        frappe.throw("不支持的课包获取类型。")
    operation = "购买授予" if package.acquisition_type == "购买" else "赠送授予"
    values = {
        "doctype": CREDIT_ENTRY_DOCTYPE,
        "student": package.student,
        "student_name_snapshot": frappe.db.get_value("MX Student", package.student, "student_name"),
        "student_package": package.name,
        "package_plan": package.package_plan,
        "plan_name_snapshot": package.plan_name_snapshot,
        "course": package.course,
        "course_name_snapshot": package.course_name_snapshot,
        "operation_type": operation,
        "effect": cint(package.credits_granted),
        "source_doctype": "MX Student Package",
        "source_name": package.name,
        "idempotency_key": f"package-grant:{package.name}",
        "demo_batch": package.demo_batch,
    }
    entry = insert_credit_idempotent(values)
    if not package.activated_at:
        activated_at = now_datetime()
        if package.docstatus == 0:
            package.activated_at = activated_at
        else:
            frappe.db.set_value(
                "MX Student Package", package.name, "activated_at", activated_at,
                update_modified=False,
            )
            package.activated_at = activated_at
    return entry
