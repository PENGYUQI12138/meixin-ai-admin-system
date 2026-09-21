import frappe
from frappe.model.document import Document
from frappe.utils import cint


class MXLessonConsumptionEntry(Document):
    def validate(self):
        if not getattr(frappe.flags, "mx_consumption_write", False):
            frappe.throw("课消流水只能由上课执行流程生成，不能直接新增或修改。", frappe.PermissionError)
        if self.operation_type == "决定":
            if cint(self.effect) not in {0, 1} or self.reversal_of:
                frappe.throw("课消决定只能使用 +1 或 0，且不能引用撤销来源。")
        elif self.operation_type == "撤销":
            if cint(self.effect) not in {-1, 0} or not self.reversal_of:
                frappe.throw("撤销流水必须引用原流水，效果只能是 -1 或 0。")
        else:
            frappe.throw("不支持的课消流水操作类型。")

    def before_save(self):
        if not self.is_new():
            frappe.throw("课消流水不可修改；纠错只能追加撤销流水。")

    def on_trash(self):
        frappe.throw("课消流水不可删除；纠错只能追加撤销流水。")
