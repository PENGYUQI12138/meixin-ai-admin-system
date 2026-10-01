import frappe
from frappe.model.document import Document
from frappe.utils import cint


class MXTeacherHourEntry(Document):
    def validate(self):
        if not getattr(frappe.flags, "mx_teacher_hour_write", False):
            frappe.throw("教师课时只能由执行单确认流程生成。", frappe.PermissionError)
        if self.operation_type == "确认":
            if cint(self.effect_minutes) < 0 or self.reversal_of:
                frappe.throw("教师课时确认记录无效。")
        elif self.operation_type == "撤销":
            if cint(self.effect_minutes) > 0 or not self.reversal_of:
                frappe.throw("教师课时撤销记录无效。")
        else:
            frappe.throw("不支持的教师课时操作。")

    def before_save(self):
        if not self.is_new():
            frappe.throw("教师课时记录不可修改；纠错须撤销执行单并修订。")

    def on_trash(self):
        frappe.throw("教师课时记录不可删除。")
