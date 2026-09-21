import frappe
from frappe.model.document import Document
from frappe.utils import cint


class MXLessonCreditEntry(Document):
    def validate(self):
        if not getattr(frappe.flags, "mx_credit_write", False):
            frappe.throw("课时权益流水只能由受控业务流程生成，不能直接新增或修改。", frappe.PermissionError)
        if not cint(self.effect):
            frappe.throw("课时权益流水效果不能为 0。")
        if self.operation_type == "M2 课消扣减" and cint(self.effect) != -1:
            frappe.throw("M2 课消扣减的权益效果必须为 -1。")
        if self.operation_type == "M2 reversal 返还" and cint(self.effect) != 1:
            frappe.throw("M2 reversal 返还的权益效果必须为 +1。")
        if self.operation_type == "人工调整" and not (self.reason or "").strip():
            frappe.throw("人工课时调整必须填写原因。")

    def before_save(self):
        if not self.is_new():
            frappe.throw("课时权益流水不可修改；纠错只能追加反向流水。")

    def on_trash(self):
        frappe.throw("课时权益流水不可删除；纠错只能追加反向流水。")
