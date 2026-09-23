"""M3 immutable receipt, reversal and refund-close services."""
from decimal import Decimal

import frappe
from frappe.utils import get_datetime, now_datetime

from meixin_admin.permissions import require_manager, require_member
from meixin_admin.scheduling import schedule_write
from meixin_admin.money import decimal_amount

PAYMENT_DOCTYPE = "MX Payment"


def locked_net_paid(student_package):
    """Return authoritative submitted cash effect while schedule_write is held."""
    return locked_payment_state(student_package)["net_paid"]


def locked_payment_state(student_package):
    rows = frappe.db.sql(
        f"""SELECT name, operation_type, cash_effect, reversal_of
            FROM `tab{PAYMENT_DOCTYPE}`
            WHERE student_package=%s AND docstatus=1
            ORDER BY creation, name FOR UPDATE""",
        (student_package,), as_dict=True,
    )
    reversed_payments = {
        row.reversal_of for row in rows
        if row.operation_type == "撤销" and row.reversal_of
    }
    return {
        "net_paid": sum((decimal_amount(row.cash_effect) for row in rows), Decimal("0")),
        "active_refund": next((
            row.name for row in rows
            if row.operation_type == "退款关闭课包" and row.name not in reversed_payments
        ), None),
    }


def _locked_package(name):
    rows = frappe.db.sql(
        "SELECT name FROM `tabMX Student Package` WHERE name=%s FOR UPDATE",
        (name,), as_dict=True,
    )
    if not rows:
        frappe.throw("学生课包不存在。")
    package = frappe.get_doc("MX Student Package", name)
    package.check_permission("read")
    if package.docstatus != 1:
        frappe.throw("付款只能关联已提交的学生课包。")
    return package


def _finalize_receipt(payment, package, state):
    if package.docstatus != 1 or package.acquisition_type != "购买":
        frappe.throw("普通收款只能关联已提交的购买型学生课包。")
    if state["active_refund"]:
        frappe.throw("课包已经退款关闭；如退款有误，请先由 Manager 撤销原退款。")
    payment.cash_effect = decimal_amount(payment.amount)
    new_total = state["net_paid"] + decimal_amount(payment.cash_effect)
    deal_amount = decimal_amount(package.deal_amount)
    if new_total < 0:
        frappe.throw("净收款不能小于 0。")
    if new_total > deal_amount:
        frappe.throw("本次收款会超过课包成交金额，已停止提交。", title="禁止超额付款")
    payment.handled_by = frappe.session.user
    if new_total == deal_amount:
        from meixin_admin.entitlements import grant_package

        grant_package(package)


def _finalize_refund(payment, package, state):
    require_manager()
    if state["active_refund"]:
        frappe.throw("该课包已经退款关闭，不能重复退款。")
    amount = decimal_amount(payment.amount)
    if amount <= 0 or amount > state["net_paid"]:
        frappe.throw("退款金额必须大于 0 且不得超过当前有效净收款。")
    payment.cash_effect = -amount
    payment.handled_by = frappe.session.user
    from meixin_admin.entitlements import reclaim_refund_credits

    reclaim_refund_credits(package, payment)


def _finalize_reversal(payment, package, state):
    require_manager()
    rows = frappe.db.sql(
        f"SELECT name FROM `tab{PAYMENT_DOCTYPE}` WHERE name=%s FOR UPDATE",
        (payment.reversal_of,), as_dict=True,
    )
    if not rows:
        frappe.throw("原付款流水不存在。")
    original = frappe.get_doc(PAYMENT_DOCTYPE, payment.reversal_of)
    original.check_permission("read")
    if original.docstatus != 1 or original.operation_type == "撤销":
        frappe.throw("只能撤销已提交且尚非撤销类型的付款流水。")
    if original.student_package != package.name:
        frappe.throw("撤销流水必须与原付款属于同一学生课包。")
    other = frappe.db.get_value(
        PAYMENT_DOCTYPE, {"reversal_of": original.name, "name": ["!=", payment.name]}, "name"
    )
    if other:
        frappe.throw("原付款已经存在撤销记录，不能重复撤销。")
    if state["active_refund"] and original.name != state["active_refund"]:
        frappe.throw("课包已经退款关闭，只能撤销当前有效的退款关闭记录。")
    payment.amount = abs(decimal_amount(original.cash_effect))
    payment.currency = original.currency
    payment.cash_effect = -decimal_amount(original.cash_effect)
    payment.handled_by = frappe.session.user
    if state["net_paid"] + decimal_amount(payment.cash_effect) < 0:
        frappe.throw("撤销后净付款不能小于 0。")
    if original.operation_type == "退款关闭课包":
        from meixin_admin.entitlements import restore_refund_credits

        restore_refund_credits(original, payment)


def finalize_payment(payment):
    """Finalize one immutable cash action inside the caller's request transaction."""
    package = _locked_package(payment.student_package)
    state = locked_payment_state(package.name)
    if payment.operation_type == "收款":
        _finalize_receipt(payment, package, state)
    elif payment.operation_type == "退款关闭课包":
        _finalize_refund(payment, package, state)
    elif payment.operation_type == "撤销":
        _finalize_reversal(payment, package, state)
    else:
        frappe.throw("不支持的付款操作类型。")


def _same_payment(existing, *, student_package, amount, payment_method, paid_at):
    return (
        existing.student_package == student_package
        and existing.operation_type == "收款"
        and decimal_amount(existing.amount) == decimal_amount(amount)
        and existing.payment_method == payment_method
        and get_datetime(existing.paid_at) == get_datetime(paid_at)
    )


def _same_correction(existing, *, student_package, operation_type, amount, reason, reversal_of=None):
    return (
        existing.student_package == student_package
        and existing.operation_type == operation_type
        and decimal_amount(existing.amount) == decimal_amount(amount)
        and (existing.reason or "") == (reason or "")
        and (existing.reversal_of or "") == (reversal_of or "")
    )


def _finish_existing(existing):
    existing.check_permission("read")
    if existing.docstatus == 0:
        existing.submit()
    elif existing.docstatus != 1:
        frappe.throw("相同请求标识指向不可用的付款状态，已停止写入。")
    return existing.name


@frappe.whitelist()
def record_payment(student_package, amount, payment_method, paid_at, request_id):
    """Idempotent ordinary-receipt API for Desk and external retries."""
    require_member()
    frappe.has_permission(PAYMENT_DOCTYPE, "create", throw=True)
    request_id = (request_id or "").strip()
    if not request_id or len(request_id) > 140:
        frappe.throw("系统请求标识格式不正确。")
    with schedule_write():
        existing_name = frappe.db.get_value(PAYMENT_DOCTYPE, {"request_id": request_id})
        if existing_name:
            existing = frappe.get_doc(PAYMENT_DOCTYPE, existing_name)
            existing.check_permission("read")
            if not _same_payment(
                existing, student_package=student_package, amount=amount,
                payment_method=payment_method, paid_at=paid_at,
            ):
                frappe.throw("相同请求标识包含不同付款内容，已停止写入。", title="付款幂等冲突")
            return _finish_existing(existing)
        payment = frappe.get_doc({
            "doctype": PAYMENT_DOCTYPE,
            "student_package": student_package,
            "operation_type": "收款",
            "amount": decimal_amount(amount),
            "payment_method": payment_method,
            "paid_at": get_datetime(paid_at),
            "request_id": request_id,
        }).insert().submit()
        return payment.name


@frappe.whitelist()
def reverse_payment(payment, reason, request_id):
    require_manager()
    reason = (reason or "").strip()
    request_id = (request_id or "").strip()
    if not reason:
        frappe.throw("撤销付款必须填写原因。")
    if not request_id or len(request_id) > 140:
        frappe.throw("系统请求标识格式不正确。")
    with schedule_write():
        rows = frappe.db.sql(
            f"SELECT name FROM `tab{PAYMENT_DOCTYPE}` WHERE name=%s FOR UPDATE",
            (payment,), as_dict=True,
        )
        if not rows:
            frappe.throw("原付款流水不存在。")
        original = frappe.get_doc(PAYMENT_DOCTYPE, payment)
        original.check_permission("read")
        existing_name = frappe.db.get_value(PAYMENT_DOCTYPE, {"request_id": request_id})
        if existing_name:
            existing = frappe.get_doc(PAYMENT_DOCTYPE, existing_name)
            if not _same_correction(
                existing, student_package=original.student_package, operation_type="撤销",
                amount=abs(decimal_amount(original.cash_effect)), reason=reason,
                reversal_of=original.name,
            ):
                frappe.throw("相同请求标识包含不同撤销内容，已停止写入。", title="付款幂等冲突")
            return _finish_existing(existing)
        return frappe.get_doc({
            "doctype": PAYMENT_DOCTYPE,
            "student_package": original.student_package,
            "operation_type": "撤销",
            "amount": abs(decimal_amount(original.cash_effect)),
            "payment_method": original.payment_method,
            "paid_at": now_datetime(),
            "reversal_of": original.name,
            "reason": reason,
            "request_id": request_id,
            "demo_batch": original.demo_batch,
        }).insert().submit().name


@frappe.whitelist()
def refund_close_package(student_package, amount, reason, request_id, payment_method="其他"):
    require_manager()
    reason = (reason or "").strip()
    request_id = (request_id or "").strip()
    if not reason:
        frappe.throw("退款关闭课包必须填写原因。")
    if not request_id or len(request_id) > 140:
        frappe.throw("系统请求标识格式不正确。")
    amount = decimal_amount(amount)
    with schedule_write():
        package = _locked_package(student_package)
        existing_name = frappe.db.get_value(PAYMENT_DOCTYPE, {"request_id": request_id})
        if existing_name:
            existing = frappe.get_doc(PAYMENT_DOCTYPE, existing_name)
            if not _same_correction(
                existing, student_package=package.name, operation_type="退款关闭课包",
                amount=amount, reason=reason,
            ):
                frappe.throw("相同请求标识包含不同退款内容，已停止写入。", title="付款幂等冲突")
            return _finish_existing(existing)
        return frappe.get_doc({
            "doctype": PAYMENT_DOCTYPE,
            "student_package": package.name,
            "operation_type": "退款关闭课包",
            "amount": amount,
            "payment_method": payment_method,
            "paid_at": now_datetime(),
            "reason": reason,
            "request_id": request_id,
            "demo_batch": package.demo_batch,
        }).insert().submit().name
