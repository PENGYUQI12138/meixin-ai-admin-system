"""Native Frappe read-only list for purchased and gifted student packages."""
import frappe

from meixin_admin.entitlements import package_overview
from meixin_admin.permissions import require_member


def execute(filters=None):
    require_member()
    frappe.has_permission("MX Student Package", "read", throw=True)
    filters = filters or {}
    package_filters = {}
    for field in ("student", "course"):
        if filters.get(field):
            package_filters[field] = filters[field]
    columns = [
        {"fieldname": "name", "label": "学生课包", "fieldtype": "Link", "options": "MX Student Package", "width": 160},
        {"fieldname": "student", "label": "学生", "fieldtype": "Link", "options": "MX Student", "width": 150},
        {"fieldname": "course", "label": "课程", "fieldtype": "Link", "options": "MX Course", "width": 150},
        {"fieldname": "effective_from", "label": "生效日期", "fieldtype": "Date", "width": 110},
        {"fieldname": "expires_on", "label": "失效日期", "fieldtype": "Date", "width": 110},
        {"fieldname": "status", "label": "派生状态（今日）", "fieldtype": "Data", "width": 140},
        {"fieldname": "deal_amount", "label": "应收（CNY）", "fieldtype": "Data", "width": 150},
        {"fieldname": "paid_amount", "label": "已付净额（CNY）", "fieldtype": "Data", "width": 150},
        {"fieldname": "remaining_credits", "label": "剩余课时", "fieldtype": "Int", "width": 100},
    ]
    packages = frappe.get_list(
        "MX Student Package", filters=package_filters, fields=["name"],
        order_by="creation desc", limit_page_length=0,
    )
    rows = []
    for package in packages:
        view = package_overview(package.name)
        rows.append({"name": package.name, **{key: view[key] for key in (
            "student", "course", "effective_from", "expires_on", "status",
            "deal_amount", "paid_amount", "remaining_credits",
        )}})
    return columns, rows
