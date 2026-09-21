import frappe

from meixin_admin.consumption import reverse_execution
from meixin_admin.execution import finalize_execution, validate_execution
from meixin_admin.permissions import require_manager
from meixin_admin.scheduling import SchedulingDocument


class MXSessionExecution(SchedulingDocument):
    def validate(self):
        validate_execution(self)

    def before_submit(self):
        finalize_execution(self)

    def before_update_after_submit(self):
        frappe.throw("已完成执行单不能直接修改；请由美心管理员取消后修订。")

    def before_cancel(self):
        require_manager()
        old = self.get_doc_before_save()
        fields = ("session", "early_completion_reason", "completed_at", "completed_by", "demo_batch", "amended_from")
        changed = any((self.get(field) or "") != (old.get(field) or "") for field in fields)
        current_rows = [(row.student, row.attendance_status, row.notes, row.consumption_rule_result)
                        for row in self.attendance]
        old_rows = [(row.student, row.attendance_status, row.notes, row.consumption_rule_result)
                    for row in old.attendance]
        if changed or current_rows != old_rows:
            frappe.throw("撤销执行单时不能夹带修改；请重新加载原单后再取消。")
        reverse_execution(self)

    def on_trash(self):
        if self.docstatus != 0:
            frappe.throw("已提交或已取消的执行单必须保留，不能删除。")
