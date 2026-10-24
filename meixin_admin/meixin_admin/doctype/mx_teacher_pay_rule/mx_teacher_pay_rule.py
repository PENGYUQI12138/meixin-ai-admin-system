import frappe
from frappe.model.document import Document

from meixin_admin.payroll_permissions import require_finance
from meixin_admin.scheduling import schedule_write
from meixin_admin.teacher_payroll import validate_rule


class MXTeacherPayRule(Document):
    def insert(self, *args, **kwargs):
        require_finance()
        with schedule_write():
            return super().insert(*args, **kwargs)

    def _save(self, *args, **kwargs):
        require_finance()
        with schedule_write():
            return super()._save(*args, **kwargs)

    def validate(self):
        require_finance()
        validate_rule(self)

    def before_update_after_submit(self):
        frappe.throw("已提交工资规则不可修改，请新增生效版本。")

    def before_cancel(self):
        frappe.throw("已提交工资规则不可取消，请新增生效版本。")

    def on_trash(self):
        require_finance()
        if self.docstatus != 0:
            frappe.throw("已提交工资规则必须保留。")
