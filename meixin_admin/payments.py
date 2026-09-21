"""M3 payment aggregation primitives; business workflows arrive in stage 4."""
from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import get_datetime

from meixin_admin.entitlements import grant_package
from meixin_admin.permissions import require_member
from meixin_admin.scheduling import schedule_write

PAYMENT_DOCTYPE = "MX Payment"


def decimal_amount(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("金额格式不正确。")


def locked_net_paid(student_package):
    """Return authoritative submitted cash effect while schedule_write is held."""
    value = frappe.db.sql(
        f"SELECT COALESCE(SUM(cash_effect), 0) FROM `tab{PAYMENT_DOCTYPE}` "
        "WHERE student_package=%s AND docstatus=1 FOR UPDATE",
        (student_package,),
    )[0][0]
    return decimal_amount(value)


def finalize_receipt(payment):
    """Validate current cash under lock and grant once on first full payment."""
    if payment.operation_type != "收款":
        frappe.throw("付款撤销和退款关闭课包将在 M3 阶段 4D 开放。")
    rows = frappe.db.sql(
        "SELECT name FROM `tabMX Student Package` WHERE name=%s FOR UPDATE",
        (payment.student_package,), as_dict=True,
    )
    if not rows:
        frappe.throw("学生课包不存在。")
    package = frappe.get_doc("MX Student Package", payment.student_package)
    package.check_permission("read")
    if package.docstatus != 1 or package.acquisition_type != "购买":
        frappe.throw("普通收款只能关联已提交的购买型学生课包。")
    current = locked_net_paid(package.name)
    new_total = current + decimal_amount(payment.cash_effect)
    deal_amount = decimal_amount(package.deal_amount)
    if new_total < 0:
        frappe.throw("净收款不能小于 0。")
    if new_total > deal_amount:
        frappe.throw("本次收款会超过课包成交金额，已停止提交。", title="禁止超额付款")
    payment.handled_by = frappe.session.user
    if new_total == deal_amount:
        grant_package(package)


def _same_payment(existing, *, student_package, amount, payment_method, paid_at):
    return (
        existing.student_package == student_package
        and existing.operation_type == "收款"
        and decimal_amount(existing.amount) == decimal_amount(amount)
        and existing.payment_method == payment_method
        and get_datetime(existing.paid_at) == get_datetime(paid_at)
    )


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
            if existing.docstatus == 0:
                existing.submit()
            elif existing.docstatus != 1:
                frappe.throw("相同请求标识指向不可用的付款状态，已停止写入。")
            return existing.name
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
