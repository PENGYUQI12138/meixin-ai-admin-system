"""Payroll roles are separate from access to raw teaching and student records."""
import frappe

from meixin_admin.permissions import is_member

FINANCE_ROLE = "Meixin Finance"
PAYROLL_DOCTYPES = {
    "MX Teacher Pay Rule", "MX Teacher Pay Rate", "MX Teacher Pay Sheet",
    "MX Teacher Pay Line", "MX Teacher Pay Coverage", "MX Teacher Pay Adjustment",
    "MX Teacher Pay Payment",
}


def is_admin(user=None):
    return is_member(user)


def is_finance(user=None):
    user = user or frappe.session.user
    return user != "Guest" and (
        user == "Administrator" or FINANCE_ROLE in frappe.get_roles(user)
    )


def can_read(user=None):
    return is_admin(user) or is_finance(user)


def require_admin():
    if not is_admin():
        frappe.throw("只有美心行政角色可以核对工资来源和人数。", frappe.PermissionError)


def require_finance():
    if not is_finance():
        frappe.throw("只有美心财务可以批准工资金额或登记付款。", frappe.PermissionError)


def require_pay_read():
    if not can_read():
        frappe.throw("没有教师工资数据的读取权限。", frappe.PermissionError)


def has_permission(doc, ptype, user=None, **kwargs):
    if not can_read(user):
        return False
    if ptype in {"read", "select", "report", "export"}:
        return True
    return doc.doctype == "MX Teacher Pay Rule" and (
        ptype in {"create", "write", "submit"} and is_finance(user)
    )


def query_conditions(user=None):
    return "" if can_read(user) else "1=0"
