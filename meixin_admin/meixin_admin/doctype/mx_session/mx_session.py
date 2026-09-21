import frappe
from frappe.utils import add_to_date, cint, get_datetime

from meixin_admin.scheduling import SchedulingDocument, ensure_no_conflicts, validate_session


class MXSession(SchedulingDocument):
    def before_validate(self):
        if self.course and self.start_at and not self.end_at:
            duration = cint(frappe.db.get_value("MX Course", self.course, "default_duration_minutes"))
            if duration > 0:
                self.end_at = add_to_date(get_datetime(self.start_at), minutes=duration)

    def validate(self):
        validate_session(self)

    def before_submit(self):
        # Lock acquired before Document loads the draft. Re-read linked records
        # and confirmed sessions while holding it; transaction owns lock to end.
        validate_session(self)
        ensure_no_conflicts(self)

    def before_update_after_submit(self):
        frappe.throw("已确认排课不能直接修改，请由美心管理员取消后修订，再重新提交。")

    def before_cancel(self):
        # Frappe cancellation skips ordinary validate/update-after-submit checks.
        # Cancel must preserve history even if an API caller sends changed fields.
        old = self.get_doc_before_save()
        fields = ("course", "teacher", "room", "notes", "demo_batch", "amended_from", "title")
        changed = any((self.get(field) or "") != (old.get(field) or "") for field in fields)
        changed = changed or any(get_datetime(self.get(field)) != get_datetime(old.get(field))
                                 for field in ("start_at", "end_at"))
        changed = changed or [row.student for row in self.students] != [row.student for row in old.students]
        if changed:
            frappe.throw("取消时不能改动原排课内容。请重新加载原单并取消，再通过修订调整。")
        from meixin_admin.execution import validate_session_cancel

        validate_session_cancel(self)

    def on_trash(self):
        if self.docstatus != 0:
            frappe.throw("已确认或已取消的排课必须保留，不能删除。")
