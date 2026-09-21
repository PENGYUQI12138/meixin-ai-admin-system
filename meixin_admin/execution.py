"""M2 execution validation and immutable attendance finalization."""
import frappe
from frappe.permissions import has_permission as quiet_has_permission
from frappe.utils import get_datetime, now_datetime

from meixin_admin.consumption import create_decision, create_session_cancel_decisions
from meixin_admin.permissions import is_manager, require_member, require_manager

ATTENDANCE_STATUSES = {"到课", "请假", "缺勤", "其他"}


def locked_session(name):
    rows = frappe.db.sql(
        "SELECT * FROM `tabMX Session` WHERE name=%s FOR UPDATE", (name,), as_dict=True,
    )
    if not rows:
        frappe.throw("原排课不存在，请重新选择。")
    session = frappe.get_doc("MX Session", name)
    if not quiet_has_permission("MX Session", "read", doc=session, print_logs=False):
        frappe.throw("没有权限读取原排课。", frappe.PermissionError)
    if session.docstatus != 1:
        frappe.throw("只有已确认且未取消的排课可以记录执行结果。")
    return session


def validate_roster(execution, session):
    expected = [row.student for row in session.students]
    actual = [row.student for row in execution.attendance]
    if len(actual) != len(set(actual)):
        frappe.throw("执行单不能重复记录同一名学生。")
    if len(actual) != len(expected) or set(actual) != set(expected):
        frappe.throw("执行单学生名单必须与原排课完全一致，不能增删或替换。")
    for row in execution.attendance:
        if row.attendance_status not in ATTENDANCE_STATUSES:
            frappe.throw("每名学生必须选择到课、请假、缺勤或其他。")
        if row.attendance_status == "其他" and not (row.notes or "").strip():
            frappe.throw("考勤状态为“其他”时必须填写说明。")


def validate_execution(execution):
    require_member()
    if not execution.session:
        frappe.throw("请选择原排课。")
    session = locked_session(execution.session)
    if execution.is_new():
        canceled = frappe.db.sql(
            """SELECT name FROM `tabMX Session Execution`
               WHERE session=%s AND docstatus=2 ORDER BY creation DESC FOR UPDATE""",
            (session.name,), as_dict=True,
        )
        if canceled:
            if not is_manager():
                frappe.throw("此排课已有撤销的执行单，只有美心管理员可以修订后重新提交。",
                             frappe.PermissionError)
            if execution.amended_from != canceled[0].name:
                frappe.throw("纠正已撤销的执行结果必须从最近的执行单点击“修订”创建。")
    if execution.is_new() and not execution.attendance:
        for row in session.students:
            execution.append("attendance", {"student": row.student})
    validate_roster(execution, session)
    execution.demo_batch = session.demo_batch
    return session


def finalize_execution(execution):
    session = validate_execution(execution)
    active = frappe.db.sql(
        """SELECT name FROM `tabMX Session Execution`
           WHERE session=%s AND docstatus=1 AND name!=%s FOR UPDATE""",
        (session.name, execution.name or ""), as_dict=True,
    )
    if active:
        frappe.throw(f"原排课已有已完成执行单 {active[0].name}，不能重复完成。")
    completed_at = now_datetime()
    if completed_at < get_datetime(session.end_at):
        if not is_manager():
            frappe.throw("计划结束时间尚未到达；只有美心管理员可以填写原因后提前确认完成。")
        if not (execution.early_completion_reason or "").strip():
            frappe.throw("提前确认完成必须填写提前完成原因。")
        execution.early_completion_reason = execution.early_completion_reason.strip()
    execution.completed_at = completed_at
    execution.completed_by = frappe.session.user

    # Resolve every rule before writing the first entry, then freeze results.
    from meixin_admin.consumption import configured_rule
    resolved = {row.attendance_status: configured_rule(row.attendance_status) for row in execution.attendance}
    for row in execution.attendance:
        row.consumption_rule_result = resolved[row.attendance_status]
    for row in execution.attendance:
        entry, _ = create_decision(
            session, row.student, row.attendance_status,
            execution=execution.name, reason=row.notes, key_prefix="execution",
        )
        if entry.rule_result != row.consumption_rule_result:
            frappe.throw("考勤冻结结果与课消流水不一致，已停止提交。")


def validate_session_cancel(session):
    rows = frappe.db.sql(
        """SELECT name, docstatus FROM `tabMX Session Execution`
           WHERE session=%s AND docstatus<2 ORDER BY creation FOR UPDATE""",
        (session.name,), as_dict=True,
    )
    if rows:
        if any(row.docstatus == 1 for row in rows):
            frappe.throw("此排课已有已完成执行单，必须先由美心管理员撤销执行单，再取消原排课。")
        frappe.throw("此排课已有执行草稿，请先处理或删除草稿，再取消原排课。")
    create_session_cancel_decisions(session)


@frappe.whitelist()
def make_execution(session):
    require_member()
    from meixin_admin.scheduling import schedule_write

    with schedule_write():
        source = locked_session(session)
        existing = frappe.db.sql(
            """SELECT name FROM `tabMX Session Execution`
               WHERE session=%s AND docstatus<2 ORDER BY creation FOR UPDATE""",
            (source.name,), as_dict=True,
        )
        if existing:
            doc = frappe.get_doc("MX Session Execution", existing[0].name)
            if not quiet_has_permission("MX Session Execution", "read", doc=doc, print_logs=False):
                frappe.throw("已有无权查看的执行草稿或完成记录，请联系美心管理员。",
                             frappe.PermissionError)
            return doc.as_dict()
        canceled = frappe.db.get_value(
            "MX Session Execution", {"session": source.name, "docstatus": 2}, "name",
            order_by="creation desc",
        )
        if canceled:
            frappe.throw("此排课的执行单已撤销，请由美心管理员打开该执行单并点击“修订”。")
        doc = frappe.new_doc("MX Session Execution")
        doc.session = source.name
        doc.demo_batch = source.demo_batch
        for row in source.students:
            doc.append("attendance", {"student": row.student})
        return doc.as_dict()


@frappe.whitelist()
def get_session_execution_status(session):
    require_member()
    source = frappe.get_doc("MX Session", session)
    if not quiet_has_permission("MX Session", "read", doc=source, print_logs=False):
        frappe.throw("没有权限读取原排课。", frappe.PermissionError)
    if source.docstatus == 2:
        return {"status": "已取消"}
    visible = frappe.get_list(
        "MX Session Execution", filters={"session": source.name},
        fields=["name", "docstatus"], order_by="creation desc", limit_page_length=100,
    )
    active = next((row.name for row in visible if row.docstatus == 1), None)
    if active:
        return {"status": "已完成", "execution": active}
    latest = visible[:1]
    if latest and latest[0].docstatus == 2:
        return {"status": "待纠正", "execution": latest[0].name}
    return {"status": "待上课", "execution": latest[0].name if latest else None}
