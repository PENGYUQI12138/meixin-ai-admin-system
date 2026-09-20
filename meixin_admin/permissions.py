"""A role gate in addition to Frappe's DocPerm/User Permission checks."""
import frappe

BUSINESS_ROLES = {"Meixin Manager", "Meixin Scheduler"}


def is_manager(user=None):
    user = user or frappe.session.user
    return user == "Administrator" or "Meixin Manager" in frappe.get_roles(user)


def is_member(user=None):
    user = user or frappe.session.user
    return user != "Guest" and (
        user == "Administrator" or bool(BUSINESS_ROLES.intersection(frappe.get_roles(user)))
    )


def require_member():
    if not is_member():
        frappe.throw("没有美心行政数据的访问权限。", frappe.PermissionError)


def require_manager():
    if not is_manager():
        frappe.throw("只有美心管理员可以执行此操作。", frappe.PermissionError)


def has_permission(doc, ptype, user=None, **kwargs):
    if not is_member(user):
        return False
    if doc.doctype == "MX Settings":
        return is_manager(user)
    if ptype in {"cancel", "delete", "export", "share", "import"}:
        return is_manager(user)
    return True


def query_conditions(user=None):
    return "" if is_member(user) else "1=0"


def prevent_mx_share(doc, method=None):
    # Frappe's explicit DocShare fallback can otherwise override controller
    # denial; M1 grants access only via its business roles, not ad-hoc shares.
    if doc.share_doctype in {"MX Student", "MX Teacher", "MX Course", "MX Room",
                             "MX Session", "MX Session Student", "MX Settings"}:
        frappe.throw("美心数据不支持单独分享，请由系统管理员按需分配美心业务角色。", frappe.PermissionError)
