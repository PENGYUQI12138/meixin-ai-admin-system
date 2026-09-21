"""M3 lesson-credit ledger primitives shared by package and M2 services."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import frappe
from frappe.utils import cint, get_datetime, getdate, now_datetime

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


def _locked_package_state(package):
    credits = frappe.db.sql(
        f"""SELECT operation_type, effect, source_doctype, source_name
            FROM `tab{CREDIT_ENTRY_DOCTYPE}`
            WHERE student_package=%s ORDER BY creation, name FOR UPDATE""",
        (package.name,), as_dict=True,
    )
    expected_grant = "购买授予" if package.acquisition_type == "购买" else "赠送授予"
    grants = [
        row for row in credits
        if row.operation_type == expected_grant
        and row.source_doctype == "MX Student Package"
        and row.source_name == package.name
        and cint(row.effect) == cint(package.credits_granted)
    ]
    state = {
        "balance": sum(cint(row.effect) for row in credits),
        "has_grant": len(grants) == 1,
        "net_paid": Decimal("0"),
        "refund_closed": False,
    }
    if package.acquisition_type == "购买":
        payments = frappe.db.sql(
            """SELECT name, operation_type, cash_effect, reversal_of
               FROM `tabMX Payment`
               WHERE student_package=%s AND docstatus=1
               ORDER BY creation, name FOR UPDATE""",
            (package.name,), as_dict=True,
        )
        state["net_paid"] = sum((Decimal(str(row.cash_effect or 0)) for row in payments), Decimal("0"))
        reversed_payments = {
            row.reversal_of for row in payments
            if row.operation_type == "撤销" and row.reversal_of
        }
        state["refund_closed"] = any(
            row.operation_type == "退款关闭课包" and row.name not in reversed_payments
            for row in payments
        )
    return state


def _throw_no_candidate(reasons):
    messages = {
        "refund_closed": "该课程课包已经退款关闭，不能继续扣课。请续费或新购课包。",
        "frozen": "该课程课包处于欠费冻结：已授予权益但当前净付款低于成交金额。请补足付款后重新提交执行单。",
        "unpaid": "该课程购买型课包尚未付清。请补足付款，或续费、新购课包。",
        "future": "该课程课包尚未生效。请核对生效日期，或使用当前有效的新课包。",
        "expired": "该课程课包已经过期。失效日当天仍可使用，次日起不可扣课；请续费或新购课包。",
        "exhausted": "该课程课包权益已耗尽。请续费、新购课包，或由 Manager 创建合法赠送包。",
        "invalid_grant": "该课程课包尚未产生合法初始权益。请检查满款激活或赠送授予流程。",
        "invalid_metadata": "该课程课包的有效期或类型元数据异常。请由 Manager 检查历史数据后再提交。",
    }
    for reason in (
        "refund_closed", "frozen", "unpaid", "future", "expired", "exhausted",
        "invalid_grant", "invalid_metadata",
    ):
        if reason in reasons:
            frappe.throw(messages[reason])
    frappe.throw("没有购买该课程的已提交课包。请新购该课程课包或由 Manager 创建合法赠送包。")


def _select_eligible_package(consumption):
    rows = frappe.db.sql(
        """SELECT name, student, package_plan, plan_name_snapshot, course,
                  course_name_snapshot, acquisition_type, credits_granted, deal_amount,
                  effective_from, expires_on, activated_at, demo_batch
           FROM `tabMX Student Package`
           WHERE student=%s AND docstatus=1
           ORDER BY name FOR UPDATE""",
        (consumption.student,), as_dict=True,
    )
    matching = [package for package in rows if package.course == consumption.course]
    if not matching:
        _throw_no_candidate(set())
    lesson_date = getdate(consumption.scheduled_start)
    eligible = []
    reasons = set()
    for package in matching:
        if package.acquisition_type not in {"购买", "赠送"} or not package.effective_from \
                or package.expires_on and getdate(package.expires_on) < getdate(package.effective_from):
            reasons.add("invalid_metadata")
            continue
        state = _locked_package_state(package)
        if state["refund_closed"]:
            reasons.add("refund_closed")
            continue
        if package.acquisition_type == "购买" and state["has_grant"] \
                and state["net_paid"] < Decimal(str(package.deal_amount)):
            reasons.add("frozen")
            continue
        if state["has_grant"] and not package.activated_at:
            reasons.add("invalid_grant")
            continue
        if not state["has_grant"]:
            reasons.add(
                "unpaid" if package.acquisition_type == "购买"
                and state["net_paid"] < Decimal(str(package.deal_amount)) else "invalid_grant"
            )
            continue
        if lesson_date < getdate(package.effective_from):
            reasons.add("future")
            continue
        if package.expires_on and lesson_date > getdate(package.expires_on):
            reasons.add("expired")
            continue
        if state["balance"] < 1:
            reasons.add("exhausted")
            continue
        eligible.append(package)
    if not eligible:
        _throw_no_candidate(reasons)
    eligible.sort(key=lambda package: (
        package.expires_on is None,
        getdate(package.expires_on) if package.expires_on else date.max,
        get_datetime(package.activated_at),
        package.name,
    ))
    return eligible[0]


def consume_m2_entry(consumption):
    """Create the one credit debit corresponding to an M2 +1 decision."""
    if cint(consumption.effect) != 1 or consumption.operation_type != "决定":
        frappe.throw("只有 M2 +1 决定可以扣减课时权益。")
    key = f"m2-consume:{consumption.name}"
    existing = _existing_m2_credit(consumption, "M2 课消扣减", -1, key)
    if existing:
        return existing
    package = _select_eligible_package(consumption)
    if locked_credit_balance(package.name) < 1:
        frappe.throw("所选课包权益刚刚耗尽，本次执行已回滚。请重新提交以选择下一可用课包。")
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
