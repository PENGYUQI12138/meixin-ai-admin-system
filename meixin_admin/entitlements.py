"""M3 lesson-credit ledger primitives shared by package and M2 services."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import cint, get_datetime, getdate, now_datetime, nowdate

from meixin_admin.permissions import require_manager, require_member
from meixin_admin.scheduling import schedule_write
from meixin_admin.money import decimal_amount

CREDIT_ENTRY_DOCTYPE = "MX Lesson Credit Entry"


@frappe.whitelist()
def create_student_package(student, package_plan, acquisition_type, effective_from,
                           expires_on=None, source_reference=None, request_id=None):
    """Create one purchase intent, reusing the same request after network retry."""
    require_member()
    frappe.has_permission("MX Student Package", "create", throw=True)
    request_id = (request_id or "").strip()
    if not request_id or len(request_id) > 140:
        frappe.throw("首次创建学生课包必须提供稳定的系统请求标识。")
    effective_from = getdate(effective_from)
    expires_on = getdate(expires_on) if expires_on else None
    source_reference = (source_reference or "").strip()
    with schedule_write():
        name = frappe.db.get_value("MX Student Package", {"request_id": request_id})
        if name:
            existing = frappe.get_doc("MX Student Package", name)
            existing.check_permission("read")
            if (existing.student != student or existing.package_plan != package_plan
                    or existing.acquisition_type != acquisition_type
                    or getdate(existing.effective_from) != effective_from
                    or (getdate(existing.expires_on) if existing.expires_on else None) != expires_on
                    or (existing.source_reference or "").strip() != source_reference):
                frappe.throw("相同请求标识包含不同课包内容，已停止创建。")
            return existing.name
        return frappe.get_doc({
            "doctype": "MX Student Package", "student": student,
            "package_plan": package_plan, "acquisition_type": acquisition_type,
            "effective_from": effective_from, "expires_on": expires_on,
            "source_reference": source_reference, "request_id": request_id,
        }).insert().name


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


def _initial_grant(package, credits):
    """Reject every malformed claim to this package's initial grant."""
    expected_operation = "购买授予" if package.acquisition_type == "购买" else "赠送授予"
    expected_key = f"package-grant:{package.name}"
    claims = [row for row in credits if row.operation_type in {"购买授予", "赠送授予"}
              or row.idempotency_key == expected_key
              or row.source_doctype == "MX Student Package"
              and row.source_name == package.name and row.operation_type != "人工调整"]
    if len(claims) > 1:
        frappe.throw("初始权益异常：课包存在多条初始授予流水，请由 Manager 检查历史。")
    if not claims:
        return None
    row = claims[0]
    expected = {
        "student": package.student, "student_package": package.name,
        "package_plan": package.package_plan, "plan_name_snapshot": package.plan_name_snapshot,
        "course": package.course, "course_name_snapshot": package.course_name_snapshot,
        "operation_type": expected_operation, "effect": cint(package.credits_granted),
        "source_doctype": "MX Student Package", "source_name": package.name,
        "idempotency_key": expected_key,
    }
    if any(row.get(field) != value for field, value in expected.items()):
        frappe.throw("初始权益异常：授予流水与购买快照不一致，请由 Manager 检查历史。")
    return row


@frappe.whitelist()
def package_overview(student_package, include_entries=0):
    """Permission-checked, read-only Desk snapshot; never used to authorize a write."""
    require_member()
    package = frappe.get_doc("MX Student Package", student_package)
    package.check_permission("read")
    payment_filters = {"student_package": package.name, "docstatus": 1}
    credit_filters = {"student_package": package.name}
    payments = frappe.get_list(
        "MX Payment", filters=payment_filters,
        fields=["name", "operation_type", "cash_effect", "reversal_of", "payment_method",
                "paid_at", "reason"], order_by="creation desc, name desc", limit_page_length=0,
    )
    credits = frappe.get_list(
        CREDIT_ENTRY_DOCTYPE, filters=credit_filters,
        fields=["name", "creation", "student", "student_package", "package_plan",
                "plan_name_snapshot", "course", "course_name_snapshot", "operation_type",
                "effect", "source_doctype", "source_name", "idempotency_key"],
        order_by="creation desc, name desc", limit_page_length=0,
    )
    if (len(payments) != frappe.db.count("MX Payment", payment_filters)
            or len(credits) != frappe.db.count(CREDIT_ENTRY_DOCTYPE, credit_filters)):
        frappe.throw("无权查看该课包的完整账务明细。", frappe.PermissionError)

    paid = sum((decimal_amount(row.cash_effect) for row in payments), Decimal("0"))
    deal = decimal_amount(package.deal_amount)
    due = max(deal - paid, Decimal("0"))
    balance = sum(cint(row.effect) for row in credits)
    try:
        grant = _initial_grant(package, credits)
        valid_grant = bool(grant and package.activated_at)
    except frappe.ValidationError:
        valid_grant = False
    reversed_payments = {row.reversal_of for row in payments if row.reversal_of}
    refund_closed = any(
        row.operation_type == "退款关闭课包" and row.name not in reversed_payments
        for row in payments
    )
    today = getdate(nowdate())
    if package.docstatus == 0:
        status = "草稿"
    elif package.docstatus == 2:
        status = "已取消"
    elif refund_closed:
        status = "已退款关闭"
    elif not valid_grant:
        status = ("待付款" if not credits and package.acquisition_type == "购买"
                  and paid < deal else "初始权益异常")
    elif balance < 0 or not package.effective_from or (
        package.expires_on and getdate(package.expires_on) < getdate(package.effective_from)
    ):
        status = "课包资料异常"
    elif package.acquisition_type == "购买" and paid < deal:
        status = "欠费冻结"
    elif today < getdate(package.effective_from):
        status = "尚未生效"
    elif package.expires_on and today > getdate(package.expires_on):
        status = "已过期"
    elif balance == 0:
        status = "已耗尽"
    else:
        status = "生效"

    return {
        "student": package.student,
        "course": package.course,
        "course_name": package.course_name_snapshot,
        "effective_from": str(package.effective_from or ""),
        "expires_on": str(package.expires_on or ""),
        "status": status,
        "deal_amount": f"¥{deal:,.2f}",
        "paid_amount": f"¥{paid:,.2f}",
        "paid_amount_value": f"{paid:.2f}",
        "due_amount": f"¥{due:,.2f}",
        "due_amount_value": f"{due:.2f}",
        "remaining_credits": balance if valid_grant else None,
        "payments": [
            {"name": row.name, "paid_at": str(row.paid_at or ""),
             "operation_type": row.operation_type, "payment_method": row.payment_method,
             "cash_effect": f"¥{decimal_amount(row.cash_effect):,.2f}",
             "note": row.reason or "", "reversal_of": row.reversal_of or ""}
            for row in payments
        ] if cint(include_entries) else [],
        "credits": [
            {"name": row.name, "creation": str(row.creation),
             "operation_type": row.operation_type, "effect": row.effect,
             "source_doctype": row.source_doctype, "source_name": row.source_name}
            for row in credits
        ] if cint(include_entries) else [],
    }


def grant_package(package):
    """Grant a package exactly once; caller already owns schedule_write."""
    if package.acquisition_type not in {"购买", "赠送"}:
        frappe.throw("不支持的课包获取类型。")
    operation = "购买授予" if package.acquisition_type == "购买" else "赠送授予"
    credits = frappe.db.sql(
        f"""SELECT student, student_package, package_plan, plan_name_snapshot, course,
                   course_name_snapshot, operation_type, effect, source_doctype,
                   source_name, idempotency_key
            FROM `tab{CREDIT_ENTRY_DOCTYPE}` WHERE student_package=%s FOR UPDATE""",
        (package.name,), as_dict=True,
    )
    existing = _initial_grant(package, credits)
    if existing:
        if not package.activated_at:
            frappe.throw("初始权益异常：已有授予流水但首次激活时间缺失，不能补写 FIFO 时间。")
        return frappe.get_doc(CREDIT_ENTRY_DOCTYPE, {"idempotency_key": existing.idempotency_key})
    if package.activated_at:
        frappe.throw("初始权益异常：激活时间存在但授予流水缺失。")
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
        f"""SELECT student, student_package, package_plan, plan_name_snapshot, course,
                   course_name_snapshot, operation_type, effect, source_doctype,
                   source_name, idempotency_key
            FROM `tab{CREDIT_ENTRY_DOCTYPE}`
            WHERE student_package=%s ORDER BY creation, name FOR UPDATE""",
        (package.name,), as_dict=True,
    )
    grant = _initial_grant(package, credits)
    state = {
        "balance": sum(cint(row.effect) for row in credits),
        "has_grant": bool(grant),
        "net_paid": Decimal("0"),
        "refund_closed": False,
    }
    if package.acquisition_type == "购买":
        from meixin_admin.payments import locked_payment_state

        payment_state = locked_payment_state(package.name)
        state["net_paid"] = payment_state["net_paid"]
        state["refund_closed"] = bool(payment_state["active_refund"])
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
                and state["net_paid"] < decimal_amount(package.deal_amount):
            reasons.add("frozen")
            continue
        if state["has_grant"] and not package.activated_at:
            reasons.add("invalid_grant")
            continue
        if not state["has_grant"]:
            reasons.add(
                "unpaid" if package.acquisition_type == "购买"
                and state["net_paid"] < decimal_amount(package.deal_amount) else "invalid_grant"
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


def reclaim_refund_credits(package, payment):
    """Snapshot and reclaim all remaining credit for one refund-close Payment."""
    balance = _locked_package_state(package)["balance"]
    if balance < 0:
        frappe.throw("课包权益余额异常，已停止退款关闭；请由 Manager 检查历史流水。")
    payment.credits_reclaimed = balance
    if balance == 0:
        return None
    values = {
        "doctype": CREDIT_ENTRY_DOCTYPE,
        "student": package.student,
        "student_name_snapshot": frappe.db.get_value("MX Student", package.student, "student_name"),
        "student_package": package.name,
        "package_plan": package.package_plan,
        "plan_name_snapshot": package.plan_name_snapshot,
        "course": package.course,
        "course_name_snapshot": package.course_name_snapshot,
        "operation_type": "退款收回",
        "effect": -balance,
        "source_doctype": "MX Payment",
        "source_name": payment.name,
        "reason": payment.reason,
        "idempotency_key": f"payment-refund-reclaim:{payment.name}",
        "demo_batch": package.demo_batch,
    }
    return insert_credit_idempotent(values)


def restore_refund_credits(original_refund, reversal):
    """Restore exactly the credit snapshot reclaimed by the original refund."""
    reclaimed = cint(original_refund.credits_reclaimed)
    if reclaimed < 0:
        frappe.throw("原退款的权益收回快照异常，已停止撤销。")
    reclaim_name = frappe.db.get_value(
        CREDIT_ENTRY_DOCTYPE,
        {"source_doctype": "MX Payment", "source_name": original_refund.name,
         "operation_type": "退款收回"},
        "name",
    )
    if reclaimed == 0:
        if reclaim_name:
            frappe.throw("原退款的权益收回快照与流水不一致，已停止撤销。")
        return None
    if not reclaim_name:
        frappe.throw("原退款缺少权益收回流水，已停止撤销。")
    reclaim = frappe.get_doc(CREDIT_ENTRY_DOCTYPE, reclaim_name)
    if cint(reclaim.effect) != -reclaimed or reclaim.student_package != original_refund.student_package:
        frappe.throw("原退款的权益收回快照与流水不一致，已停止撤销。")
    values = {
        "doctype": CREDIT_ENTRY_DOCTYPE,
        "student": reclaim.student,
        "student_name_snapshot": reclaim.student_name_snapshot,
        "student_package": reclaim.student_package,
        "package_plan": reclaim.package_plan,
        "plan_name_snapshot": reclaim.plan_name_snapshot,
        "course": reclaim.course,
        "course_name_snapshot": reclaim.course_name_snapshot,
        "operation_type": "退款 reversal 恢复",
        "effect": reclaimed,
        "source_doctype": "MX Payment",
        "source_name": reversal.name,
        "reversal_of": reclaim.name,
        "reason": reversal.reason,
        "idempotency_key": f"payment-reversal-restore:{reversal.name}",
        "demo_batch": reclaim.demo_batch,
    }
    return insert_credit_idempotent(values)


def _integer_effect(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("人工调整课时必须是非零整数。")
    if not parsed.is_finite() or parsed != parsed.to_integral_value() or parsed == 0:
        frappe.throw("人工调整课时必须是非零整数。")
    return int(parsed)


@frappe.whitelist()
def adjust_credits(student_package, effect, reason, request_id):
    require_manager()
    effect = _integer_effect(effect)
    reason = (reason or "").strip()
    request_id = (request_id or "").strip()
    if not reason:
        frappe.throw("人工课时调整必须填写原因。")
    if not request_id or len(request_id) > 120:
        frappe.throw("系统请求标识格式不正确。")
    with schedule_write():
        rows = frappe.db.sql(
            "SELECT name FROM `tabMX Student Package` WHERE name=%s FOR UPDATE",
            (student_package,), as_dict=True,
        )
        if not rows:
            frappe.throw("学生课包不存在。")
        package = frappe.get_doc("MX Student Package", student_package)
        package.check_permission("read")
        if package.docstatus != 1:
            frappe.throw("只能调整已提交的学生课包。")
        values = {
            "doctype": CREDIT_ENTRY_DOCTYPE,
            "student": package.student,
            "student_name_snapshot": frappe.db.get_value("MX Student", package.student, "student_name"),
            "student_package": package.name,
            "package_plan": package.package_plan,
            "plan_name_snapshot": package.plan_name_snapshot,
            "course": package.course,
            "course_name_snapshot": package.course_name_snapshot,
            "operation_type": "人工调整",
            "effect": effect,
            "source_doctype": "MX Student Package",
            "source_name": package.name,
            "reason": reason,
            "idempotency_key": f"manual-adjustment:{request_id}",
            "demo_batch": package.demo_batch,
        }
        existing_name = frappe.db.get_value(
            CREDIT_ENTRY_DOCTYPE, {"idempotency_key": values["idempotency_key"]}
        )
        if existing_name:
            existing = frappe.get_doc(CREDIT_ENTRY_DOCTYPE, existing_name)
            if not _same_credit(existing, values):
                frappe.throw("相同请求标识包含不同调整内容，已停止写入。", title="权益调整幂等冲突")
            return existing.name
        state = _locked_package_state(package)
        if state["refund_closed"]:
            frappe.throw("已退款关闭的课包不能人工调整；如退款有误，请撤销原退款。")
        if not state["has_grant"] or not package.activated_at:
            frappe.throw("课包尚未产生合法初始权益，不能人工调整。")
        if state["balance"] + effect < 0:
            frappe.throw("人工负调整会导致课时权益余额小于 0，已停止写入。")
        return insert_credit_idempotent(values).name
