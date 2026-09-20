"""Non-destructive schema setup. No demo data or user accounts."""
import frappe

DOCTYPES = ["MX Student", "MX Teacher", "MX Course", "MX Room", "MX Session",
            "MX Session Student", "MX Settings"]


def before_install():
    if frappe.db.db_type != "mariadb":
        frappe.throw("M1 已验证的数据库为 MariaDB；请先在隔离环境验证其他数据库。")
    if int(frappe.__version__.split(".")[0]) != 16:
        frappe.throw("此版本适配 Frappe 16，请先核对现场版本，不要升级或覆盖现有 App。")
    existing = [dt for dt in DOCTYPES if frappe.db.exists("DocType", dt)]
    if existing:
        frappe.throw("发现已有 MX 类型，请人工检查兼容性后迁移；安装不会覆盖：" + "、".join(existing))
    if frappe.db.exists("Module Def", "Meixin Admin"):
        frappe.throw("已存在 Meixin Admin 模块，请先检查其来源。")


def after_install():
    after_migrate()


def after_migrate():
    # Schema sync and DocPerm are source controlled; only fill an absent default.
    if not frappe.db.get_single_value("MX Settings", "institution_name"):
        frappe.db.set_single_value("MX Settings", "institution_name", "美心")
    frappe.db.add_index("MX Session", ["docstatus", "start_at", "end_at"], "mx_session_period")
    frappe.db.add_index("MX Session Student", ["student", "parent"], "mx_student_session")

