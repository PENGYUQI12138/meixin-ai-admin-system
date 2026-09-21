import frappe

from meixin_admin.payments import decimal_amount
from meixin_admin.permissions import is_manager, require_member
from meixin_admin.scheduling import SchedulingDocument


class MXPayment(SchedulingDocument):
    def before_insert(self):
        self.request_id = self.request_id or frappe.generate_hash(length=32)

    def validate(self):
        require_member()
        previous = self.get_doc_before_save()
        if previous and self.request_id != previous.request_id:
            frappe.throw("系统请求标识不可修改。")
        if self.operation_type not in {"收款", "退款关闭课包", "撤销"}:
            frappe.throw("不支持的付款操作类型。")
        if self.operation_type != "收款" and not is_manager():
            frappe.throw("只有美心管理员可以退款关闭课包或撤销付款。", frappe.PermissionError)
        package = frappe.get_doc("MX Student Package", self.student_package)
        package.check_permission("read")
        if package.docstatus != 1:
            frappe.throw("付款只能关联已提交的学生课包。")
        self.student = package.student
        self.currency = package.currency
        amount = decimal_amount(self.amount)
        if amount <= 0:
            frappe.throw("付款金额必须大于 0。")
        if self.currency != "CNY":
            frappe.throw("M3 当前仅支持人民币 CNY。")
        if self.operation_type == "收款":
            self.cash_effect = amount
            self.reversal_of = None
        elif self.operation_type == "退款关闭课包":
            if not (self.reason or "").strip():
                frappe.throw("退款关闭课包必须填写原因。")
            self.cash_effect = -amount
            self.reversal_of = None
        else:
            if not (self.reason or "").strip():
                frappe.throw("撤销付款必须填写原因。")
            if not self.reversal_of:
                frappe.throw("撤销必须引用原付款流水。")
            original = frappe.get_doc("MX Payment", self.reversal_of)
            original.check_permission("read")
            if original.docstatus != 1 or original.operation_type == "撤销":
                frappe.throw("只能撤销已提交且尚非撤销类型的付款流水。")
            if original.student_package != self.student_package:
                frappe.throw("撤销流水必须与原付款属于同一学生课包。")
            self.amount = abs(decimal_amount(original.cash_effect))
            self.currency = original.currency
            self.cash_effect = -decimal_amount(original.cash_effect)

    def before_update_after_submit(self):
        frappe.throw("已提交付款不可修改；纠错只能追加撤销流水。")

    def before_cancel(self):
        frappe.throw("付款不能直接取消；纠错只能追加撤销流水。")

    def on_trash(self):
        if self.docstatus != 0:
            frappe.throw("已提交付款必须保留，不能删除。")
