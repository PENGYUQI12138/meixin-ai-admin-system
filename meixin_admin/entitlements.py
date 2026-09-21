"""M3 lesson-credit ledger primitives shared by package and M2 services."""
from contextlib import contextmanager

import frappe
from frappe.utils import cint, getdate, now_datetime

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


def _existing_m2_credit(consumption, operation, effect, key):
    name = frappe.db.get_value(CREDIT_ENTRY_DOCTYPE, {"idempotency_key": key})
    if not name:
        return None
    entry = frappe.get_doc(CREDIT_ENTRY_DOCTYPE, name)
    expected = {
        "student": consumption.student,
        "course": consumption.course,
        "operation_type": operation,
        "effect": effect,
        "source_doctype": "MX Lesson Consumption Entry",
        "source_name": consumption.name,
        "m2_consumption_entry": consumption.name,
        "idempotency_key": key,
    }
    if not all((entry.get(field) or "") == (value or "") for field, value in expected.items()):
        frappe.throw("M2 课消幂等键已被不同的权益流水占用，已停止写入。", title="课时权益冲突")
    return entry


def _single_eligible_package(consumption):
    rows = frappe.db.sql(
        """SELECT name, student, package_plan, plan_name_snapshot, course,
                  course_name_snapshot, effective_from, expires_on, activated_at, demo_batch
           FROM `tabMX Student Package`
           WHERE student=%s AND course=%s AND docstatus=1
           ORDER BY name FOR UPDATE""",
        (consumption.student, consumption.course), as_dict=True,
    )
    lesson_date = getdate(consumption.scheduled_start)
    eligible = []
    for package in rows:
        if not package.activated_at:
            continue
        if lesson_date < getdate(package.effective_from):
            continue
        if package.expires_on and lesson_date > getdate(package.expires_on):
            continue
        if locked_credit_balance(package.name) >= 1:
            eligible.append(package)
    if not eligible:
        frappe.throw(
            "没有可扣减的有效课包：请检查课程是否匹配、课包是否已付清并激活、"
            "上课日期是否在有效期内，以及剩余课时是否充足。"
        )
    if len(eligible) != 1:
        frappe.throw("存在多个可扣减课包；请等待课包分配规则完成后再提交，系统不会猜测扣减来源。")
    return eligible[0]


def consume_m2_entry(consumption):
    """Create the one credit debit corresponding to an M2 +1 decision."""
    if cint(consumption.effect) != 1 or consumption.operation_type != "决定":
        frappe.throw("只有 M2 +1 决定可以扣减课时权益。")
    key = f"m2-consume:{consumption.name}"
    existing = _existing_m2_credit(consumption, "M2 课消扣减", -1, key)
    if existing:
        return existing
    package = _single_eligible_package(consumption)
    values = {
        "doctype": CREDIT_ENTRY_DOCTYPE,
        "student": consumption.student,
        "student_name_snapshot": consumption.student_name_snapshot,
        "student_package": package.name,
        "package_plan": package.package_plan,
        "plan_name_snapshot": package.plan_name_snapshot,
        "course": consumption.course,
        "course_name_snapshot": consumption.course_name_snapshot,
        "operation_type": "M2 课消扣减",
        "effect": -1,
        "source_doctype": "MX Lesson Consumption Entry",
        "source_name": consumption.name,
        "m2_consumption_entry": consumption.name,
        "reason": consumption.reason,
        "idempotency_key": key,
        "demo_batch": package.demo_batch,
    }
    return insert_credit_idempotent(values)


def restore_m2_entry(original, reversal):
    """Return credit to the exact package debited by the original M2 decision."""
    if cint(original.effect) != 1 or cint(reversal.effect) != -1:
        frappe.throw("只有 M2 +1 决定的 -1 reversal 可以返还课时权益。")
    key = f"m2-restore:{reversal.name}"
    existing = _existing_m2_credit(reversal, "M2 reversal 返还", 1, key)
    if existing:
        return existing
    debit_name = frappe.db.get_value(
        CREDIT_ENTRY_DOCTYPE,
        {"m2_consumption_entry": original.name, "operation_type": "M2 课消扣减"},
        "name",
    )
    if not debit_name:
        frappe.throw("原 M2 课消没有对应的权益扣减，无法执行返还。")
    debit = frappe.get_doc(CREDIT_ENTRY_DOCTYPE, debit_name)
    if (debit.student, debit.course) != (original.student, original.course):
        frappe.throw("原 M2 课消与权益扣减的学生或课程不一致，已停止返还。", title="课时权益冲突")
    values = {
        "doctype": CREDIT_ENTRY_DOCTYPE,
        "student": reversal.student,
        "student_name_snapshot": reversal.student_name_snapshot,
        "student_package": debit.student_package,
        "package_plan": debit.package_plan,
        "plan_name_snapshot": debit.plan_name_snapshot,
        "course": reversal.course,
        "course_name_snapshot": reversal.course_name_snapshot,
        "operation_type": "M2 reversal 返还",
        "effect": 1,
        "source_doctype": "MX Lesson Consumption Entry",
        "source_name": reversal.name,
        "m2_consumption_entry": reversal.name,
        "reversal_of": debit.name,
        "reason": reversal.reason,
        "idempotency_key": key,
        "demo_batch": debit.demo_batch,
    }
    return insert_credit_idempotent(values)
