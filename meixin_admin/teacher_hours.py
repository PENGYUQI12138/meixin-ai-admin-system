"""One immutable teaching decision per completed execution, plus reversals."""
from contextlib import contextmanager

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from meixin_admin.permissions import is_manager, require_member
from meixin_admin.scheduling import checked_period, schedule_write

DOCTYPE = "MX Teacher Hour Entry"


@contextmanager
def entry_write():
    previous = getattr(frappe.flags, "mx_teacher_hour_write", False)
    frappe.flags.mx_teacher_hour_write = True
    try:
        yield
    finally:
        frappe.flags.mx_teacher_hour_write = previous


def _insert(values):
    with entry_write():
        return frappe.get_doc({"doctype": DOCTYPE, **values}).insert(ignore_permissions=True)


@frappe.whitelist()
def confirm_teaching(execution, actual_start=None, actual_end=None, taught=1, exception_reason=None):
    require_member()
    with schedule_write():
        rows = frappe.db.sql(
            "SELECT name, session, docstatus FROM `tabMX Session Execution` WHERE name=%s FOR UPDATE",
            (execution,), as_dict=True,
        )
        if not rows or rows[0].docstatus != 1:
            frappe.throw("只能确认已完成且未撤销执行单的教师授课课时。")
        source = frappe.get_doc("MX Session Execution", execution)
        if not frappe.has_permission("MX Session Execution", "read", doc=source):
            frappe.throw("没有权限读取执行单。", frappe.PermissionError)
        prior = frappe.db.sql(
            f"SELECT name FROM `tab{DOCTYPE}` WHERE execution=%s AND operation_type='确认' FOR UPDATE",
            (execution,), as_dict=True,
        )
        if prior:
            frappe.throw(f"执行单已有教师课时记录 {prior[0].name}，不能重复确认。")
        session = frappe.get_doc("MX Session", source.session)
        for doc in (session, frappe.get_doc("MX Teacher", session.teacher),
                    frappe.get_doc("MX Course", session.course)):
            if not frappe.has_permission(doc.doctype, "read", doc=doc):
                frappe.throw("没有权限使用关联排课或档案。", frappe.PermissionError)
        taught = cint(taught)
        if taught not in (0, 1):
            frappe.throw("是否实际授课必须为 0 或 1。")
        present = any(row.attendance_status == "到课" for row in source.attendance)
        reason = (exception_reason or "").strip()
        if taught:
            start, end = checked_period(actual_start, actual_end)
            if end > now_datetime():
                frappe.throw("实际授课结束时间不能晚于当前时间。")
            minutes = int((end - start).total_seconds() // 60)
            if minutes < 1 or minutes > 1440:
                frappe.throw("实际授课时长必须在 1 至 1440 分钟之间。")
            planned_start, planned_end = get_datetime(session.start_at), get_datetime(session.end_at)
            unusual = (not present or start < planned_start or end > planned_end)
        else:
            if actual_start or actual_end:
                frappe.throw("未实际授课不能填写授课起止时间。")
            if present:
                frappe.throw("学生已标记到课，不能确认未实际授课；请先修订执行单。")
            start = end = None
            minutes = 0
            unusual = True
        if unusual and (not is_manager() or not reason):
            frappe.throw("异常授课记录须由美心管理员填写原因并确认。", frappe.PermissionError)
        return _insert({
            "execution": source.name, "session": session.name,
            "teacher": session.teacher,
            "teacher_name_snapshot": frappe.db.get_value("MX Teacher", session.teacher, "teacher_name"),
            "course": session.course,
            "course_name_snapshot": frappe.db.get_value("MX Course", session.course, "course_name"),
            "scheduled_start": session.start_at, "scheduled_end": session.end_at,
            "actual_start": start, "actual_end": end,
            "operation_type": "确认", "effect_minutes": minutes,
            "exception_reason": reason if unusual else None,
            "confirmed_by": frappe.session.user, "confirmed_at": now_datetime(),
            "idempotency_key": f"teacher-confirm:{source.name}",
            "demo_batch": session.demo_batch,
        }).name


def reverse_execution_hours(execution):
    rows = frappe.db.sql(
        f"SELECT name FROM `tab{DOCTYPE}` WHERE execution=%s AND operation_type='确认' FOR UPDATE",
        (execution.name,), as_dict=True,
    )
    if not rows:
        return None
    original = frappe.get_doc(DOCTYPE, rows[0].name)
    prior = frappe.db.get_value(DOCTYPE, {"reversal_of": original.name}, "name")
    if prior:
        frappe.throw("教师课时确认已撤销，执行单状态不一致。")
    values = {field: original.get(field) for field in (
        "execution", "session", "teacher", "teacher_name_snapshot", "course",
        "course_name_snapshot", "scheduled_start", "scheduled_end", "actual_start",
        "actual_end", "demo_batch",
    )}
    values.update(
        operation_type="撤销", effect_minutes=-cint(original.effect_minutes),
        reversal_of=original.name, confirmed_by=frappe.session.user,
        confirmed_at=now_datetime(), exception_reason=f"撤销执行单 {execution.name}",
        idempotency_key=f"teacher-reversal:{original.name}",
    )
    return _insert(values)
