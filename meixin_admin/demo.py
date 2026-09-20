"""手动演示初始化及只读清理预览；安装、迁移均不调用本模块。"""

import re
from datetime import timedelta

import frappe
from frappe.utils import getdate, nowdate

from meixin_admin.scheduling import acquire_schedule_lock

DEFAULT_BATCH = "MX-M1-DEMO-001"
EXPECTED = {"MX Student": 6, "MX Teacher": 2, "MX Course": 2, "MX Room": 2, "MX Session": 3}


def _authorize(batch):
    if frappe.session.user == "Guest" or (
        frappe.session.user != "Administrator" and "Meixin Manager" not in frappe.get_roles()
    ):
        frappe.throw("只有美心管理员可以初始化演示数据或查看清理预览。", frappe.PermissionError)
    if not isinstance(batch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", batch):
        frappe.throw("演示批次只允许 1 至 64 位英文字母、数字、下划线和短横线。")


def _records(batch, *, current=False):
    if current:
        result = {}
        for doctype in EXPECTED:
            frappe.has_permission(doctype, "read", throw=True)
            names = frappe.db.sql(
                f"SELECT name FROM `tab{doctype}` WHERE demo_batch=%s ORDER BY creation, name FOR UPDATE", (batch,)
            )
            for name, in names:
                frappe.get_doc(doctype, name).check_permission("read")
            result[doctype] = [name for name, in names]
        return result
    return {
        doctype: frappe.get_list(
            doctype, filters={"demo_batch": batch}, pluck="name", order_by="creation, name", limit_page_length=0
        )
        for doctype in EXPECTED
    }


def initialize(batch=DEFAULT_BATCH, start_date=None):
    """创建整批虚构数据，重复调用不增加记录；调用者负责最终 commit/rollback。

    示例：bench --site <已确认试点站点> execute meixin_admin.demo.initialize
    可传 --kwargs '{"batch":"MX-M1-DEMO-001","start_date":"2026-10-03"}'。
    默认从站点当前日期之后的第一个星期六开始，采用站点实际时区。
    """
    _authorize(batch)
    acquire_schedule_lock()
    records = _records(batch, current=True)
    if any(records.values()):
        if any(len(records[doctype]) != count for doctype, count in EXPECTED.items()):
            frappe.throw("该演示批次记录不完整或数量异常；请先运行清理预览并人工核对，不会覆盖或补造数据。")
        return {"batch": batch, "created": False, "counts": EXPECTED.copy(), "records": records}

    today = getdate(nowdate())
    day = getdate(start_date) if start_date else today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    savepoint = "mx_demo_" + frappe.generate_hash(length=12)
    frappe.db.savepoint(savepoint)
    try:
        def add(doctype, **fields):
            return frappe.get_doc({"doctype": doctype, "demo_batch": batch, **fields}).insert().name

        students = [
            add("MX Student", student_name=f"演示学生{number}", grade="演示年级", notes="虚构演示资料", enabled=1)
            for number in range(1, 7)
        ]
        teachers = [add("MX Teacher", teacher_name=f"演示教师{number}", enabled=1) for number in (1, 2)]
        courses = [
            add("MX Course", course_name=f"演示课程{number}", subject="虚构科目", default_duration_minutes=60, enabled=1)
            for number in (1, 2)
        ]
        rooms = [add("MX Room", room_name=f"演示教室{number}", capacity=6, enabled=1) for number in (1, 2)]
        for index in range(3):
            resource = 0 if index < 2 else 1
            frappe.get_doc(
                {
                    "doctype": "MX Session",
                    "demo_batch": batch,
                    "course": courses[resource],
                    "teacher": teachers[resource],
                    "room": rooms[resource],
                    "start_at": f"{day} {9 + index:02}:00:00",
                    "end_at": f"{day} {10 + index:02}:00:00",
                    "students": [{"student": name} for name in students[index * 2 : index * 2 + 2]],
                    "notes": f"虚构演示排课，第 {index + 1} 节；批次 {batch}",
                }
            ).insert().submit()
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return {"batch": batch, "created": True, "start_date": str(day), "counts": EXPECTED.copy(), "records": _records(batch)}


def preview_cleanup(batch=DEFAULT_BATCH):
    """只列出批次记录与引用它们的排课；不取消、不删除、不更改任何数据。"""
    _authorize(batch)
    records = _records(batch)
    sessions = set(records["MX Session"])
    for doctype, fieldname in (("MX Teacher", "teacher"), ("MX Course", "course"), ("MX Room", "room")):
        if records[doctype]:
            sessions.update(frappe.get_list("MX Session", filters={fieldname: ["in", records[doctype]]}, pluck="name", limit_page_length=0))
    if records["MX Student"]:
        # 子表通过有权限的父排课读取；不开放通用子表导出接口。
        for name in frappe.get_list("MX Session", pluck="name", limit_page_length=0):
            session = frappe.get_doc("MX Session", name)
            session.check_permission("read")
            if any(row.student in records["MX Student"] for row in session.students):
                sessions.add(name)
    references = []
    for name in sorted(sessions):
        session = frappe.get_doc("MX Session", name)
        session.check_permission("read")
        references.append({"name": name, "docstatus": session.docstatus, "start_at": str(session.start_at), "end_at": str(session.end_at), "demo_batch": session.demo_batch})
    return {
        "batch": batch,
        "preview_only": True,
        "counts": {doctype: len(names) for doctype, names in records.items()},
        "records": records,
        "referencing_sessions": references,
        "instruction": "此操作仅预览。本轮不自动删除；先核对其他批次引用，再由管理员另行决定取消及清理。",
    }
