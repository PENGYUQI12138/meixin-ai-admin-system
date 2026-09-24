"""CNY business amounts use exact cents before Frappe or MariaDB storage."""
from decimal import Decimal, InvalidOperation

import frappe

CENT = Decimal("0.01")
# Frappe 16.34 casts Currency through float: below 10^13 its max half-ULP
# is < 0.001 CNY, safely below half a cent before MariaDB DECIMAL(21,2).
MAX_CNY = Decimal("9999999999999.99")


def decimal_amount(value):
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        frappe.throw("人民币金额格式或范围不正确。")
    if not amount.is_finite():
        frappe.throw("人民币金额格式或范围不正确。")
    if amount.as_tuple().exponent < -2 or abs(amount) > MAX_CNY:
        frappe.throw("人民币金额精度必须为分（小数点后两位），且不得超出字段范围。")
    return amount.quantize(CENT)
