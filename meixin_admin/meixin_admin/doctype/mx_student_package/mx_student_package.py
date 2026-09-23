import frappe
from frappe.utils import cint, getdate

from meixin_admin.permissions import is_manager, require_member, require_manager
from meixin_admin.scheduling import SchedulingDocument
from meixin_admin.money import decimal_amount


class MXStudentPackage(SchedulingDocument):
    def before_insert(self):
        self.request_id = (self.request_id or "").strip()
        if not self.request_id or len(self.request_id) > 140:
            frappe.throw("首次创建学生课包必须提供稳定的系统请求标识。")

    def validate(self):
        require_member()
        previous = self.get_doc_before_save()
        if previous and self.request_id != previous.request_id:
            frappe.throw("系统请求标识不可修改。")
        if previous:
            immutable = (
                "student", "package_plan", "acquisition_type",
                "source_reference", "plan_name_snapshot", "course",
                "course_name_snapshot", "credits_granted", "currency",
            )
            changed = [field for field in immutable if self.get(field) != previous.get(field)]
            if decimal_amount(self.deal_amount) != decimal_amount(previous.deal_amount):
                changed.append("deal_amount")
            for field in ("effective_from", "expires_on"):
                current_date = getdate(self.get(field)) if self.get(field) else None
                previous_date = getdate(previous.get(field)) if previous.get(field) else None
                if current_date != previous_date:
                    changed.append(field)
            if changed:
                frappe.throw(f"学生课包购买内容和成交快照不可修改（{', '.join(changed)}）；续费请新建课包。")
        if self.acquisition_type not in {"购买", "赠送"}:
            frappe.throw("课包获取类型只能是购买或赠送。")
        if self.acquisition_type == "赠送" and not is_manager():
            frappe.throw("只有美心管理员可以创建赠送课包。", frappe.PermissionError)
        plan = frappe.get_doc("MX Package Plan", self.package_plan)
        plan.check_permission("read")
        if not cint(plan.enabled):
            frappe.throw("所选课包计划已停用。")
        student = frappe.get_doc("MX Student", self.student)
        student.check_permission("read")
        if not cint(student.enabled):
            frappe.throw("所选学生已停用。")
        if not previous:
            self.plan_name_snapshot = plan.plan_name
            self.course = plan.course
            self.course_name_snapshot = frappe.db.get_value("MX Course", plan.course, "course_name")
            self.credits_granted = plan.standard_credits
            self.currency = plan.currency
            self.deal_amount = 0 if self.acquisition_type == "赠送" else plan.standard_price
        self.deal_amount = decimal_amount(self.deal_amount)
        if cint(self.credits_granted) <= 0:
            frappe.throw("获得课时数必须大于 0。")
        if self.acquisition_type == "购买" and self.deal_amount <= 0:
            frappe.throw("购买课包的成交金额必须大于 0。")
        if self.acquisition_type == "赠送" and self.deal_amount != 0:
            frappe.throw("赠送课包的成交金额必须为 0。")
        if self.currency != "CNY":
            frappe.throw("M3 当前仅支持人民币 CNY。")
        if self.expires_on and getdate(self.expires_on) < getdate(self.effective_from):
            frappe.throw("失效日期不能早于生效日期。")

    def before_submit(self):
        if self.acquisition_type == "赠送":
            require_manager()
            from meixin_admin.entitlements import grant_package

            grant_package(self)

    def before_cancel(self):
        require_manager()
        if frappe.db.exists("MX Payment", {"student_package": self.name}):
            frappe.throw("已有付款流水的学生课包不能取消。")
        if frappe.db.exists("MX Lesson Credit Entry", {"student_package": self.name}):
            frappe.throw("已有课时权益流水的学生课包不能取消。")

    def on_trash(self):
        if self.docstatus != 0:
            frappe.throw("已提交或已取消的学生课包必须保留，不能删除。")
