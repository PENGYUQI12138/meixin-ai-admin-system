"""Native payroll report; the service applies the same read gate to API/export."""
import frappe

from meixin_admin.payroll_permissions import require_pay_read
from meixin_admin.teacher_payroll import get_pay_report


def execute(filters=None):
    require_pay_read()
    frappe.has_permission("MX Teacher Pay Sheet", "report", throw=True)
    return get_pay_report(filters or {})
