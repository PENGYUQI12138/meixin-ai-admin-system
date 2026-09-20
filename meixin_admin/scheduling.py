"""MariaDB scheduling invariants, enforced within the request transaction."""
from html import escape
from contextlib import contextmanager

import frappe
from frappe.model.document import Document
from frappe.permissions import has_permission as quiet_has_permission
from frappe.utils import cint, get_datetime, now_datetime

from meixin_admin.permissions import require_member

MASTER_FIELDS = {
    "MX Student": "student_name", "MX Teacher": "teacher_name",
    "MX Course": "course_name", "MX Room": "room_name",
}


def acquire_schedule_lock():
    """Lock a stable metadata row until COMMIT/ROLLBACK, never a timed Redis lease.

    Every App write takes this BEFORE Document locks its own row. Reading current
    values with FOR UPDATE after the mutex also handles MariaDB REPEATABLE READ.
    """
    if frappe.db.db_type != "mariadb":
        frappe.throw("当前数据库未经 M1 并发验证，已停止排课写入。")
    # ponytail: one site-wide write lock suits 4 admins; split by resource only
    # after measured contention, preserving a deterministic lock order.
    rows = frappe.db.sql("SELECT name FROM `tabDocType` WHERE name=%s FOR UPDATE", ("MX Session",))
    if not rows:
        frappe.throw("美心排课类型尚未安装完成，请先完成迁移。")


class SchedulingDocument(Document):
    def insert(self, *args, **kwargs):
        require_member()
        with schedule_write():
            return super().insert(*args, **kwargs)

    def _save(self, *args, **kwargs):
        require_member()
        with schedule_write():
            return super()._save(*args, **kwargs)


@contextmanager
def schedule_write():
    try:
        acquire_schedule_lock()
        yield
    except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
        # MariaDB 11.8 snapshot isolation may abort a locking read (error 1020)
        # after another writer commits. Fail the whole request, never continue
        # on the stale snapshot or retry only half of a transaction.
        frappe.db.rollback()
        frappe.throw("其他行政刚刚更新了排课或档案，本次操作已回滚。请重新加载后再提交；服务器会重新检查冲突。",
                     title="排课数据已变化")


def checked_period(start, end):
    try:
        if not start or not end:
            raise ValueError
        start, end = get_datetime(start), get_datetime(end)
        if not start or not end or start.tzinfo or end.tzinfo or end <= start:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        frappe.throw("结束时间必须晚于开始时间；请按站点时区填写有效的起止时间。")
    return start, end


def current_master(doctype, name, *, check_permission=True):
    if doctype not in MASTER_FIELDS:
        raise ValueError("Unsupported master")
    if not name:
        frappe.throw("请完整选择课程、教师、教室和学生。")
    # Identifiers only come from the constant allowlist above, values parameterized.
    rows = frappe.db.sql(f"SELECT * FROM `tab{doctype}` WHERE name=%s FOR UPDATE", (name,), as_dict=True)
    if not rows:
        frappe.throw("所选档案不存在，请重新选择。")
    row = rows[0]
    if check_permission and not quiet_has_permission(
        doctype, "read", doc=frappe.get_doc(doctype, name), print_logs=False
    ):
        frappe.throw("没有权限使用所选档案。", frappe.PermissionError)
    if not cint(row.enabled):
        frappe.throw(f"{escape(frappe._(doctype))}「{escape(row[MASTER_FIELDS[doctype]])}」已停用，请重新选择。")
    return row


def validate_session(doc):
    start, end = checked_period(doc.start_at, doc.end_at)
    if not doc.students:
        frappe.throw("排课至少需要一名学生。")
    student_ids = [row.student for row in doc.students]
    if len(set(student_ids)) != len(student_ids):
        frappe.throw("同一节课不能重复添加同一名学生。")
    course = current_master("MX Course", doc.course)
    teacher = current_master("MX Teacher", doc.teacher)
    room = current_master("MX Room", doc.room)
    for student in student_ids:
        current_master("MX Student", student)
    if len(student_ids) > cint(room.capacity):
        frappe.throw(f"排课人数 {len(student_ids)} 超过教室容量 {cint(room.capacity)}，请调整名单或教室。")
    doc.start_at, doc.end_at = start, end
    doc.title = f"{course.course_name} · {teacher.teacher_name}"


def conflicts(doc):
    start, end = checked_period(doc.start_at, doc.end_at)
    # Current/locking reads, not consistent-snapshot reads. The site mutex is
    # already held before any Document row locks and before this re-read.
    rows = frappe.db.sql(
        """SELECT name, teacher, room, start_at, end_at FROM `tabMX Session`
           WHERE docstatus=1 AND name!=%s AND start_at<%s AND end_at>%s
           ORDER BY start_at, name FOR UPDATE""", (doc.name or "", end, start), as_dict=True,
    )
    wanted = {row.student for row in doc.students}
    result = []
    for old in rows:
        resources = []
        if old.teacher == doc.teacher:
            resources.append(("教师", "MX Teacher", old.teacher))
        if old.room == doc.room:
            resources.append(("教室", "MX Room", old.room))
        children = frappe.db.sql(
            """SELECT student FROM `tabMX Session Student`
               WHERE parent=%s AND parenttype='MX Session' AND parentfield='students' FOR UPDATE""",
            (old.name,),
        )
        for student, in children:
            if student in wanted:
                resources.append(("学生", "MX Student", student))
        if resources:
            result.append((old, resources))
    return result


def conflict_message(old, resources):
    # Global detection is necessary; disclose details only with doc read access.
    old_doc = frappe.get_doc("MX Session", old.name)
    if not quiet_has_permission("MX Session", "read", doc=old_doc, print_logs=False):
        return "所选资源存在无权查看的已确认排课，请联系美心管理员协调。"
    parts = []
    for label, dt, name in resources:
        master = frappe.get_doc(dt, name)
        if quiet_has_permission(dt, "read", doc=master, print_logs=False):
            parts.append(f"{label}「{escape(master.get(MASTER_FIELDS[dt]))}」")
        else:
            parts.append(label)
    return (f"{'、'.join(parts)}与已确认排课「{escape(old.name)}」冲突："
            f"{old.start_at:%Y-%m-%d %H:%M} 至 {old.end_at:%Y-%m-%d %H:%M}（站点时区）。")


def ensure_no_conflicts(doc):
    found = conflicts(doc)
    if found:
        frappe.throw(conflict_message(*found[0]), title="排课冲突")


def validate_master(doc):
    if doc.doctype == "MX Course" and cint(doc.default_duration_minutes) <= 0:
        frappe.throw("默认课时分钟数必须大于 0。")
    if doc.doctype == "MX Room" and cint(doc.capacity) <= 0:
        frappe.throw("教室容量必须大于 0。")
    if doc.doctype == "MX Teacher":
        doc.user = doc.user or None  # SQL UNIQUE allows multiple NULLs, never repeated empty strings.
    previous = doc.get_doc_before_save()
    if not previous:
        return
    disabling = cint(previous.enabled) and not cint(doc.enabled)
    shrinking = doc.doctype == "MX Room" and cint(doc.capacity) < cint(previous.capacity)
    if not disabling and not shrinking:
        return
    if doc.doctype == "MX Student":
        rows = frappe.db.sql(
            """SELECT s.name FROM `tabMX Session` s
               WHERE s.docstatus=1 AND s.end_at>%s AND EXISTS
                 (SELECT 1 FROM `tabMX Session Student` ss WHERE ss.parent=s.name
                  AND ss.parenttype='MX Session' AND ss.parentfield='students' AND ss.student=%s)
               FOR UPDATE""", (now_datetime(), doc.name), as_dict=True,
        )
    else:
        field = {"MX Teacher": "teacher", "MX Course": "course", "MX Room": "room"}[doc.doctype]
        rows = frappe.db.sql(
            f"SELECT name FROM `tabMX Session` WHERE docstatus=1 AND end_at>%s AND `{field}`=%s FOR UPDATE",
            (now_datetime(), doc.name), as_dict=True,
        )
    if disabling and rows:
        frappe.throw("此档案被未来或正在进行的已确认排课使用，不能停用；请先由管理员取消或修订相关排课。")
    if shrinking:
        for row in rows:
            students = frappe.db.sql(
                "SELECT name FROM `tabMX Session Student` WHERE parent=%s AND parenttype='MX Session' AND parentfield='students' FOR UPDATE",
                (row.name,),
            )
            if len(students) > cint(doc.capacity):
                frappe.throw("缩小容量会使未来或正在进行的已确认排课超员，请先调整排课。")
