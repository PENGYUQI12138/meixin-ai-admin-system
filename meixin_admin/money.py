"""CNY business amounts use exact cents before Frappe or MariaDB storage."""
from decimal import Decimal, InvalidOperation

import frappe

CENT = Decimal("0.01")
MAX_CNY = Decimal("9999999999999999999.99")  # Currency DocField: DECIMAL(21,2).


def decimal_amount(value):
    try:
        amount = Decimal(str(value if value is not None else 0))
        cents = amount.quantize(CENT)
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("人民币金额格式或范围不正确。")
    if not amount.is_finite() or amount != cents or abs(cents) > MAX_CNY:
        frappe.throw("人民币金额精度必须为分（小数点后两位），且不得超出字段范围。")
    return cents
