"""Authenticated Desk endpoints. Frappe list and document permissions apply."""
import frappe
from frappe.permissions import has_permission as quiet_has_permission
from frappe.utils import get_system_timezone

from meixin_admin.permissions import require_member
from meixin_admin.scheduling import checked_period


@frappe.whitelist()
def get_context():
    require_member()
    site_zone = get_system_timezone()
    return {
        "time_zone": site_zone,
        "user_time_zone": frappe.db.get_value("User", frappe.session.user, "time_zone") or site_zone,
        "institution_name": frappe.db.get_single_value("MX Settings", "institution_name") or "美心",
    }


@frappe.whitelist()
def get_events(start, end, filters=None, doctype=None, fields=None, field_map=None):
    require_member()
    frappe.has_permission("MX Session", "read", throw=True)
    start, end = checked_period(start, end)
    if (end - start).days > 93:
        frappe.throw("一次最多查询 93 天课表，请缩小日期范围。")
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)
    predicates = [["docstatus", "<", 2], ["start_at", "<", end], ["end_at", ">", start]]
    # Native Calendar supplies a list of [doctype, field, operator, value].
    # Only exact teacher/room filters are accepted; no caller-controlled fields.
    if isinstance(filters, dict):
        items = list(filters.items())
    elif isinstance(filters, list):
        items = []
        for entry in filters:
            if not isinstance(entry, (list, tuple)) or len(entry) not in (3, 4):
                frappe.throw("课表筛选格式不正确。")
            field, op, value = entry[-3:]
            if op != "=":
                frappe.throw("课表只支持教师、教室的精确筛选。")
            items.append((field, value))
    elif filters is None:
        items = []
    else:
        frappe.throw("课表筛选格式不正确。")
    for field, value in items:
        if field not in {"teacher", "room"}:
            frappe.throw("课表只支持教师、教室筛选。")
        if value:
            if not isinstance(value, str):
                frappe.throw("请选择有效的筛选档案。")
            predicates.append([field, "=", value])
    rows = frappe.get_list("MX Session", filters=predicates,
                           fields=["name", "title", "start_at", "end_at", "docstatus"],
                           order_by="start_at asc", limit_page_length=0)
    result = []
    for row in rows:
        doc = frappe.get_doc("MX Session", row.name)
        if not quiet_has_permission("MX Session", "read", doc=doc, print_logs=False):
            continue
        result.append({
            "name": row.name, "title": f"{'确认' if row.docstatus == 1 else '草稿 · 不占用'}｜{row.title}",
            "start": row.start_at, "end": row.end_at,
            "start_at": row.start_at, "end_at": row.end_at,
            "docstatus": row.docstatus, "color": "#16805d" if row.docstatus == 1 else "#b7791f",
            "allDay": False,
        })
    return result
