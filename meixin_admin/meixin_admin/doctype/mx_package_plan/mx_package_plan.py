import frappe
from frappe.utils import cint

from meixin_admin.permissions import require_manager
from meixin_admin.scheduling import SchedulingDocument
from meixin_admin.money import decimal_amount


class MXPackagePlan(SchedulingDocument):
    def validate(self):
        require_manager()
        if cint(self.standard_credits) <= 0:
            frappe.throw("标准课时数必须大于 0。")
        self.standard_price = decimal_amount(self.standard_price)
        if self.standard_price <= 0:
            frappe.throw("标准价格必须大于 0；赠送通过学生赠送课包处理。")
        if self.currency != "CNY":
            frappe.throw("M3 当前仅支持人民币 CNY。")
