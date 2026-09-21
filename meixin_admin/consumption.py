"""Immutable M2 lesson-consumption decisions and reversals."""
from contextlib import contextmanager

import frappe
from frappe.utils import cint

from meixin_admin.permissions import require_manager
from meixin_admin.scheduling import schedule_write

ENTRY_DOCTYPE = "MX Lesson Consumption Entry"
RULE_FIELDS = {
    "到课": "present_rule",
    "请假": "leave_rule",
    "缺勤": "absent_rule",
    "其他": "other_rule",
    "课程取消": "session_cancel_rule",
}
RULE_RESULTS = {"课消", "不课消"}


def configured_rule(outcome):
    field = RULE_FIELDS.get(outcome)
    if not field:
        frappe.throw("不支持的考勤或课程结果。")
    result = frappe.db.get_single_value("MX Settings", field) or "未配置"
    if result not in RULE_RESULTS:
        frappe.throw(
            f"“{outcome}”的课消规则尚未配置。请由美心管理员先在机构设置中选择“课消”或“不课消”。",
            title="课消规则未配置",
        )
    return result


@contextmanager
def ledger_write():
    previous = getattr(frappe.flags, "mx_consumption_write", False)
    frappe.flags.mx_consumption_write = True
    try:
        yield
    finally:
        frappe.flags.mx_consumption_write = previous


def _entry_values(session, student, outcome, rule_result, operation_type, effect,
                  idempotency_key, *, execution=None, reversal_of=None, reason=None):
    return {
        "doctype": ENTRY_DOCTYPE,
        "student": student,
        "student_name_snapshot": frappe.db.get_value("MX Student", student, "student_name"),
        "session": session.name,
        "execution": execution,
        "course": session.course,
        "course_name_snapshot": frappe.db.get_value("MX Course", session.course, "course_name"),
        "scheduled_start": session.start_at,
        "scheduled_end": session.end_at,
        "outcome": outcome,
        "rule_result": rule_result,
        "operation_type": operation_type,
        "effect": effect,
        "reversal_of": reversal_of,
        "reason": reason,
        "idempotency_key": idempotency_key,
        "demo_batch": session.demo_batch,
    }


def _same_entry(existing, values):
    fields = (
        "student", "session", "execution", "course", "outcome", "rule_result",
        "operation_type", "effect", "reversal_of", "reason", "idempotency_key",
    )
    return all((existing.get(field) or "") == (values.get(field) or "") for field in fields)


def insert_idempotent(values):
    existing_name = frappe.db.get_value(ENTRY_DOCTYPE, {"idempotency_key": values["idempotency_key"]})
    if existing_name:
        existing = frappe.get_doc(ENTRY_DOCTYPE, existing_name)
        if not _same_entry(existing, values):
            frappe.throw("幂等键已被不同的课消内容占用，已停止写入。", title="课消数据冲突")
        return existing
    with ledger_write():
        return frappe.get_doc(values).insert(ignore_permissions=True)


def create_decision(session, student, outcome, *, execution=None, reason=None, key_prefix="execution"):
    rule_result = configured_rule(outcome)
    key = f"{key_prefix}:{execution or session.name}:{student}"
    values = _entry_values(
        session, student, outcome, rule_result, "决定", 1 if rule_result == "课消" else 0, key,
        execution=execution, reason=reason,
    )
    return insert_idempotent(values), rule_result


def create_reversal(original, *, key_prefix, reason):
    key = f"{key_prefix}:{original.name}"
    existing_name = frappe.db.get_value(ENTRY_DOCTYPE, {"idempotency_key": key})
    if existing_name:
        existing = frappe.get_doc(ENTRY_DOCTYPE, existing_name)
        if (existing.reversal_of, existing.reason or "") != (original.name, reason or ""):
            frappe.throw("相同撤销请求包含不同内容，已停止写入。", title="课消数据冲突")
        return existing
    other = frappe.db.get_value(ENTRY_DOCTYPE, {"reversal_of": original.name}, "name")
    if other:
        frappe.throw(f"课消流水 {original.name} 已由 {other} 撤销，不能重复撤销。")
    session = frappe.get_doc("MX Session", original.session)
    values = _entry_values(
        session, original.student, original.outcome, original.rule_result, "撤销",
        -1 if cint(original.effect) == 1 else 0, key,
        execution=original.execution, reversal_of=original.name, reason=reason,
    )
    return insert_idempotent(values)


def reverse_execution(execution):
    rows = frappe.db.sql(
        f"""SELECT name FROM `tab{ENTRY_DOCTYPE}`
            WHERE execution=%s AND operation_type='决定' ORDER BY name FOR UPDATE""",
        (execution.name,), as_dict=True,
    )
    if len(rows) != len(execution.attendance):
        frappe.throw("执行单的课消流水不完整，已停止撤销；请联系管理员检查。")
    return [
        create_reversal(
            frappe.get_doc(ENTRY_DOCTYPE, row.name),
            key_prefix="execution-reversal",
            reason=f"撤销执行单 {execution.name}",
        )
        for row in rows
    ]


def create_session_cancel_decisions(session):
    # Resolve once before any write so an unconfigured rule cannot leave partial rows.
    rule_result = configured_rule("课程取消")
    entries = []
    for row in session.students:
        values = _entry_values(
            session, row.student, "课程取消", rule_result, "决定",
            1 if rule_result == "课消" else 0,
            f"session-cancel:{session.name}:{row.student}",
            reason=f"取消排课 {session.name}",
        )
        entries.append(insert_idempotent(values))
    return entries


@frappe.whitelist()
def manual_reverse(entry, reason):
    require_manager()
    if not (reason or "").strip():
        frappe.throw("人工撤销课消必须填写原因。")
    with schedule_write():
        rows = frappe.db.sql(
            f"SELECT name FROM `tab{ENTRY_DOCTYPE}` WHERE name=%s FOR UPDATE", (entry,), as_dict=True,
        )
        if not rows:
            frappe.throw("课消流水不存在。")
        original = frappe.get_doc(ENTRY_DOCTYPE, entry)
        if original.operation_type != "决定" or cint(original.effect) != 1:
            frappe.throw("只能人工撤销仍有效的 +1 课消决定。")
        reversed_entry = create_reversal(
            original, key_prefix="manual-reversal", reason=reason.strip(),
        )
        return reversed_entry.name
