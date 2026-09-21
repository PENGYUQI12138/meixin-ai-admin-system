"""M3 payment aggregation primitives; business workflows arrive in stage 4."""
from decimal import Decimal, InvalidOperation

import frappe

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
        "WHERE student_package=%s AND docstatus=1",
        (student_package,),
    )[0][0]
    return decimal_amount(value)
