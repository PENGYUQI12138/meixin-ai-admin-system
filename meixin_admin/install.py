"""Non-destructive schema setup. No demo data or user accounts."""
import frappe
from frappe.utils import get_system_timezone

DOCTYPES = ["MX Student", "MX Teacher", "MX Course", "MX Room", "MX Session",
            "MX Session Student", "MX Session Execution", "MX Session Attendance",
            "MX Lesson Consumption Entry", "MX Settings", "MX Package Plan",
            "MX Student Package", "MX Payment", "MX Lesson Credit Entry", "MX Teacher Hour Entry"]


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
    frappe.db.add_index("MX Session Execution", ["session", "docstatus"], "mx_session_execution")
    frappe.db.add_index("MX Session Attendance", ["student", "parent"], "mx_student_execution")
    frappe.db.add_index("MX Lesson Consumption Entry", ["session", "student"], "mx_session_student_consumption")
    frappe.db.add_index("MX Package Plan", ["course", "enabled"], "mx_package_plan_course")
    frappe.db.add_index("MX Student Package", ["student", "course", "docstatus"], "mx_student_course_package")
    frappe.db.add_index("MX Student Package", ["effective_from", "expires_on"], "mx_package_validity")
    frappe.db.add_index("MX Payment", ["student_package", "docstatus"], "mx_package_payment")
    frappe.db.add_index("MX Lesson Credit Entry", ["student_package", "creation"], "mx_package_credit")
    frappe.db.add_index("MX Lesson Credit Entry", ["student", "course"], "mx_student_course_credit")
    frappe.db.add_index("MX Teacher Hour Entry", ["execution", "operation_type"], "mx_teacher_execution")
    frappe.db.add_index("MX Teacher Hour Entry", ["teacher", "confirmed_at"], "mx_teacher_hours_period")
    align_business_user_timezones()


def align_business_user_timezones():
    """Use Frappe's site timezone for administrators who operate Meixin data."""
    site_zone = get_system_timezone()
    users = {"Administrator"}
    users.update(frappe.get_all(
        "Has Role",
        filters={"parenttype": "User", "role": ["in", ["Meixin Manager", "Meixin Scheduler"]]},
        pluck="parent",
    ))
    for user in users:
        if frappe.db.exists("User", user) and frappe.db.get_value("User", user, "time_zone") != site_zone:
            frappe.db.set_value("User", user, "time_zone", site_zone, update_modified=False)
