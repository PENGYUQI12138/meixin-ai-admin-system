"""Teacher pay: reviewed source snapshots, immutable approval and offline cash facts."""
import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, now_datetime

from meixin_admin.money import decimal_amount
from meixin_admin.payroll_permissions import (
    is_admin, require_admin, require_finance, require_pay_read,
)
from meixin_admin.scheduling import schedule_write

RULE = "MX Teacher Pay Rule"
SHEET = "MX Teacher Pay Sheet"
COVERAGE = "MX Teacher Pay Coverage"
ADJUSTMENT = "MX Teacher Pay Adjustment"
PAYMENT = "MX Teacher Pay Payment"
HOUR = "MX Teacher Hour Entry"
INPUT_FIELDS = {
    "kind", "hour_entry", "class_size", "headcount_basis", "exception_reason",
    "amount", "pay_date", "scope", "hours", "work_key", "description",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text(value, label, limit=500, required=False):
    if value is not None and not isinstance(value, str):
        frappe.throw(f"{label}格式不正确。")
    value = (value or "").strip()
    if len(value) > limit or (required and not value):
        frappe.throw(f"{label}不能为空且不得超过 {limit} 字。" if required else f"{label}不能超过 {limit} 字。")
    return value


def _date(value):
    try:
        if not value:
            raise ValueError
        return getdate(value)
    except (ValueError, TypeError, OverflowError):
        frappe.throw("请填写有效业务日期。")


def _integer(value, label):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        frappe.throw(f"{label}必须为非负整数。")
    number = int(value)
    if number > 1000000:
        frappe.throw(f"{label}超出范围。")
    return number


def _inputs(lines):
    if isinstance(lines, str):
        try:
            lines = json.loads(lines)
        except (ValueError, TypeError):
            frappe.throw("工资明细格式不正确。")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 200:
        frappe.throw("每张单需要 1 至 200 条工资明细。")
    for row in lines:
        if not isinstance(row, dict) or set(row) - INPUT_FIELDS:
            frappe.throw("工资明细含不支持的字段。")
    return lines


@contextmanager
def _write():
    old = getattr(frappe.flags, "mx_payroll_write", False)
    frappe.flags.mx_payroll_write = True
    try:
        yield
    finally:
        frappe.flags.mx_payroll_write = old


class PayrollDocument(Document):
    """No direct REST, import, set_value or ordinary Document write path."""
    def insert(self, *args, **kwargs):
        self._guard()
        return super().insert(*args, **kwargs)

    def _save(self, *args, **kwargs):
        self._guard()
        return super()._save(*args, **kwargs)

    def _guard(self):
        if not getattr(frappe.flags, "mx_payroll_write", False):
            frappe.throw("工资记录只能通过受控核对、批准及付款操作写入。", frappe.PermissionError)

    def before_update_after_submit(self):
        frappe.throw("已批准工资原账不可修改，请追加调整。")

    def before_cancel(self):
        frappe.throw("工资原账不可取消，请追加调整或付款冲正。")

    def on_trash(self):
        frappe.throw("工资及付款记录必须保留。")


def _save(doc, submit=False):
    with _write():
        doc.flags.ignore_permissions = True
        if doc.is_new():
            doc.insert(ignore_permissions=True)
        elif not submit:
            doc.save(ignore_permissions=True)
        if submit:
            doc.submit()
    return doc.name


def _locked(doctype, name, lock=True):
    if doctype not in {SHEET, ADJUSTMENT, PAYMENT}:
        raise ValueError("Unsupported payroll document")
    clause = " FOR UPDATE" if lock else ""
    rows = frappe.db.sql(f"SELECT name FROM `tab{doctype}` WHERE name=%s" + clause, (name,))
    if not rows:
        frappe.throw("工资单据不存在。")
    doc = frappe.get_doc(doctype, rows[0][0], for_update=lock)
    doc.check_permission("read")
    return doc


def validate_rule(rule):
    rule.effective_from = _date(rule.effective_from)
    if rule.currency != "CNY" or not rule.rates or len(rule.rates) > 30:
        frappe.throw("工资规则仅支持人民币，且须配置人数档位。")
    next_min = 1
    for index, rate in enumerate(rule.rates):
        lower = _integer(rate.min_students, "人数下限")
        upper = _integer(rate.max_students or 0, "人数上限")
        amount = decimal_amount(rate.amount)
        if lower != next_min or (upper and upper < lower) or amount <= 0:
            frappe.throw("人数档位须从 1 开始连续、不重叠，单价必须大于零。")
        if upper == 0 and index != len(rule.rates) - 1:
            frappe.throw("无上限档位只能放在最后。")
        rate.min_students, rate.max_students, rate.amount = lower, upper, amount
        rate.currency = "CNY"
        next_min = upper + 1
    if rule.rates[-1].max_students:
        frappe.throw("最后一个人数档位须设为无上限（上限填 0）。")


def _rate(day, size):
    rows = frappe.db.sql(
        f"SELECT name FROM `tab{RULE}` WHERE docstatus=1 AND effective_from<=%s "
        "ORDER BY effective_from DESC LIMIT 1 FOR UPDATE", (day,),
    )
    if not rows:
        return None, None, "缺少授课当日有效的已提交工资规则"
    rule = frappe.get_doc(RULE, rows[0][0])
    snapshot = {
        "name": rule.name, "effective_from": str(rule.effective_from), "unit_minutes": 90,
        "rates": [{"min": r.min_students, "max": r.max_students,
                   "amount": str(decimal_amount(r.amount))} for r in rule.rates],
    }
    for rate in rule.rates:
        if rate.min_students <= size and (not rate.max_students or size <= rate.max_students):
            return rule.name, snapshot, decimal_amount(rate.amount)
    return rule.name, snapshot, "计薪人数不在有效规则范围内"


def _chain(doctype, name, lock=True):
    if doctype not in {"MX Session", "MX Session Execution"}:
        raise ValueError("Unsupported source chain")
    seen = set()
    while name:
        if name in seen or len(seen) >= 100:
            frappe.throw("授课来源修订链异常，请先核查。")
        seen.add(name)
        clause = " FOR UPDATE" if lock else ""
        rows = frappe.db.sql(f"SELECT name, amended_from FROM `tab{doctype}` WHERE name=%s" + clause, (name,))
        if not rows:
            frappe.throw("授课来源修订链缺失，请先核查。")
        if not rows[0][1]:
            return rows[0][0]
        name = rows[0][1]


def _source_root(hour, lock=True):
    root = _chain("MX Session", hour.session, lock)
    execution_root = _chain("MX Session Execution", hour.execution, lock)
    original_session = frappe.db.get_value("MX Session Execution", execution_root, "session")
    if _chain("MX Session", original_session, lock) != root:
        frappe.throw("执行单修订链跨越了不同原始排课，请先核查。")
    return root


def _source(name, teacher, check_read=False, lock=True):
    name = _text(name, "教师授课确认", 140, True)
    clause = " FOR UPDATE" if lock else ""
    rows = frappe.db.sql(f"SELECT name FROM `tab{HOUR}` WHERE name=%s" + clause, (name,))
    if not rows:
        frappe.throw("教师授课确认不存在。")
    hour = frappe.get_doc(HOUR, rows[0][0])
    execution = frappe.get_doc("MX Session Execution", hour.execution)
    session = frappe.get_doc("MX Session", hour.session)
    if check_read:
        for doc in (hour, execution, session, frappe.get_doc("MX Teacher", teacher)):
            doc.check_permission("read")
    reversed_rows = frappe.db.sql(
        f"SELECT name FROM `tab{HOUR}` WHERE reversal_of=%s" + clause, (hour.name,),
    )
    if (hour.operation_type != "确认" or hour.effect_minutes <= 0 or reversed_rows
            or execution.docstatus != 1 or session.docstatus != 1):
        frappe.throw("授课来源已失效、取消或未实际授课，请重新核对。")
    if (hour.teacher != teacher or session.teacher != teacher or execution.session != session.name
            or hour.course != session.course):
        frappe.throw("授课来源教师或课程不一致，请先走来源纠错流程。")
    if not hour.actual_start or not hour.actual_end or not execution.completed_at:
        frappe.throw("授课来源缺少实际时间或完成时间，请先核查。")
    start, end = get_datetime(hour.actual_start), get_datetime(hour.actual_end)
    if end <= start or end > get_datetime(execution.completed_at) or end > now_datetime():
        frappe.throw("授课来源实际时间异常，请先核查。")
    present = any(r.attendance_status == "到课" for r in execution.attendance)
    issues = []
    if (end - start).total_seconds() != 5400:
        issues.append("实际授课不是完整90分钟")
    if (get_datetime(hour.scheduled_end) - get_datetime(hour.scheduled_start)).total_seconds() != 5400:
        issues.append("计划时长不是90分钟")
    if not present:
        issues.append("无人到课")
    if (hour.exception_reason or start < get_datetime(hour.scheduled_start)
            or end > get_datetime(hour.scheduled_end)):
        issues.append("来源有异常确认")
    return {
        "hour_entry": hour.name, "source_root": _source_root(hour, lock), "teacher": hour.teacher,
        "teacher_name_snapshot": hour.teacher_name_snapshot, "course_name_snapshot": hour.course_name_snapshot,
        "execution": hour.execution, "session": hour.session, "pay_date": str(start.date()),
        "actual_start": str(hour.actual_start), "actual_end": str(hour.actual_end),
        "scheduled_start": str(hour.scheduled_start), "scheduled_end": str(hour.scheduled_end),
        "effect_minutes": hour.effect_minutes, "roster_size": len(session.students),
        "roster_digest": _digest(sorted(r.student for r in session.students)),
        "attendance_digest": _digest(sorted((r.student, r.attendance_status) for r in execution.attendance)),
        "completed_at": str(execution.completed_at), "present_any": present, "issues": issues,
    }


def _day_hours(teacher, day):
    rows = frappe.db.sql(
        f"""SELECT h.name FROM `tab{HOUR}` h
            INNER JOIN `tabMX Session Execution` e ON e.name=h.execution AND e.docstatus=1
            INNER JOIN `tabMX Session` s ON s.name=h.session AND s.docstatus=1
            WHERE h.teacher=%s AND h.operation_type='确认' AND h.effect_minutes>0
              AND h.actual_start >= %s AND h.actual_start < %s
              AND NOT EXISTS (SELECT r.name FROM `tab{HOUR}` r WHERE r.reversal_of=h.name)
            ORDER BY h.name FOR UPDATE""", (teacher, day, day + timedelta(days=1)),
    )
    return [r[0] for r in rows]


def _claim(kind, *identity):
    return kind + ":" + _digest(identity)


def _prepare(teacher, first, last, inputs, check_read=False):
    lines, coverage = [], []
    for index, row in enumerate(inputs, 1):
        kind = row.get("kind", "课程")
        if kind not in ("课程", "特殊", "固定"):
            frappe.throw("工资明细类型不正确。")
        reason = _text(row.get("exception_reason"), "特殊金额原因")
        description = _text(row.get("description"), "覆盖范围或工作说明")
        basis = _text(row.get("headcount_basis"), "计薪人数依据")
        line = dict(kind=kind, exception_reason=reason, description=description, headcount_basis=basis, currency="CNY")
        issues, sources = [], []
        entered_amount = row.get("amount")
        if kind != "课程" and (entered_amount is None or entered_amount == ""):
            issues.append("特殊或固定费用须明确填写金额；零金额请填0")
            entered_amount = 0
        if kind in {"课程", "特殊"}:
            source = _source(row.get("hour_entry"), teacher, check_read)
            sources = [source]
            day = _date(source["pay_date"])
            line.update(hour_entry=source["hour_entry"], source_root=source["source_root"],
                        roster_size=source["roster_size"])
            size = source["roster_size"] if row.get("class_size") is None or row.get("class_size") == "" else _integer(row["class_size"], "计薪人数")
            line["class_size"] = size
            if not basis or size <= 0:
                issues.append("须由行政核对全班计薪人数并填写依据")
            if kind == "课程":
                issues.extend(source["issues"])
                if reason:
                    issues.append("有争议或特殊原因的课程须人工核对，不能自动按正常课计薪")
                rule, snapshot, result = _rate(day, size)
                line.update(rate_rule=rule, rule_snapshot=_json(snapshot) if snapshot else None)
                if isinstance(result, str):
                    issues.append(result)
                    result = Decimal("0.00")
                line["amount"] = result
            else:
                if not reason:
                    issues.append("特殊金额须填写原因并交财务批准")
                line["amount"] = decimal_amount(entered_amount)
        else:
            day = _date(row.get("pay_date"))
            scope = row.get("scope", "指定")
            if scope not in ("全天", "指定"):
                frappe.throw("固定费用必须明确全天或指定覆盖范围。")
            line.update(scope=scope, work_key=_text(row.get("work_key"), "工作事项标识", 140))
            if not reason or not description:
                issues.append("固定费用须填写覆盖约定及原因")
            hour_ids = row.get("hours") or []
            if not isinstance(hour_ids, list) or len(hour_ids) > 200:
                frappe.throw("固定费用覆盖课程列表不正确。")
            if scope == "全天":
                if hour_ids or line["work_key"]:
                    frappe.throw("全天授课覆盖不能混填指定课程或独立工作事项。")
                hour_ids = _day_hours(teacher, day)
                coverage.append(dict(teacher=teacher, pay_date=day, line_index=index,
                                     active_key=_claim("D", teacher, str(day))))
                if not hour_ids:
                    issues.append("当天没有有效授课来源，须先核对或明确为独立工作事项")
            elif not hour_ids and not line["work_key"]:
                issues.append("指定固定费用须列出课程或明确的工作事项标识")
            if line["work_key"]:
                if not description:
                    issues.append("工作事项须填写明确说明")
                coverage.append(dict(teacher=teacher, pay_date=day, line_index=index,
                                     active_key=_claim("W", teacher, line["work_key"].casefold())))
            sources = [_source(name, teacher, check_read) for name in hour_ids]
            if any(not first <= _date(s["pay_date"]) <= last for s in sources):
                frappe.throw("指定覆盖课程日期必须在本单结算范围内。")
            line["amount"] = decimal_amount(entered_amount)
        if not first <= day <= last:
            frappe.throw("工资明细日期必须在结算范围内。")
        if line["amount"] < 0:
            frappe.throw("原始工资金额不能为负，差额请通过财务调整处理。")
        line.update(pay_date=day, source_snapshot=_json(sources), review_issue="；".join(issues))
        lines.append(line)
        for source in sources:
            coverage.append(dict(teacher=teacher, pay_date=_date(source["pay_date"]), line_index=index,
                                 source_root=source["source_root"], hour_entry=source["hour_entry"],
                                 active_key=_claim("L", source["source_root"])))
    keys = [c["active_key"] for c in coverage]
    if len(keys) != len(set(keys)):
        frappe.throw("同一课程、全天或工作事项不能重复纳入同一结算单。")
    for day_row in (c for c in coverage if c["active_key"].startswith("D:")):
        if any(c["pay_date"] == day_row["pay_date"] and c["line_index"] != day_row["line_index"]
               and c["active_key"].startswith("L:") for c in coverage):
            frappe.throw("全天授课固定费用与同日课程重复。")
    total = decimal_amount(sum((l["amount"] for l in lines), Decimal("0.00")))
    return lines, coverage, total


def _assert_claims(sheet, coverage):
    rows = frappe.db.sql(
        f"SELECT active_key, pay_date, parent FROM `tab{COVERAGE}` "
        "WHERE teacher=%s AND active_key IS NOT NULL AND active_key!='' AND parent!=%s FOR UPDATE",
        (sheet.teacher, sheet.name), as_dict=True,
    )
    # Lesson identity survives a teacher correction as well as execution revisions.
    keys = [c["active_key"] for c in coverage]
    if keys:
        duplicates = frappe.db.sql(
            f"SELECT parent FROM `tab{COVERAGE}` WHERE active_key IN %(keys)s AND parent!=%(sheet)s FOR UPDATE",
            {"keys": keys, "sheet": sheet.name},
        )
        if duplicates:
            frappe.throw("来源已经被其他核对或批准单占用，禁止重复计薪。")
    for current in coverage:
        for old in rows:
            if _date(old.pay_date) != _date(current["pay_date"]):
                continue
            if ((old.active_key.startswith("D:") and current["active_key"].startswith(("D:", "L:")))
                    or (current["active_key"].startswith("D:") and old.active_key.startswith(("D:", "L:")))):
                frappe.throw("该教师当天授课已被其他结算覆盖，请重新核对覆盖范围。")


def _set_prepared(sheet, prepared, claim=False):
    lines, coverage, total = prepared
    sheet.set("lines", [dict(line) for line in lines])
    sheet.set("coverage", [dict(c, active_key=c["active_key"] if claim else None) for c in coverage])
    sheet.total_amount = total


def _new_request(doctype, request_id, payload):
    request_id = _text(request_id, "请求标识", 140, True)
    rows = frappe.db.sql(
        f"SELECT name FROM `tab{doctype}` WHERE request_id=%s FOR UPDATE", (request_id,),
    )
    if rows:
        doc = _locked(doctype, rows[0][0])
        if doc.request_payload != _json(payload):
            frappe.throw("相同请求标识对应不同内容，已拒绝重复请求。")
        return request_id, doc
    return request_id, None


@frappe.whitelist()
def create_sheet(teacher, from_date, to_date, lines, request_id):
    require_admin()
    first, last = _date(from_date), _date(to_date)
    if first > last or (last - first).days > 366:
        frappe.throw("结算日期范围须顺序正确且不超过一年。")
    inputs = _inputs(lines)
    teacher = _text(teacher, "教师", 140, True)
    payload = dict(teacher=teacher, from_date=first, to_date=last, lines=inputs)
    with schedule_write():
        request_id, prior = _new_request(SHEET, request_id, payload)
        if prior:
            return prior.name
        master = frappe.get_doc("MX Teacher", teacher)
        master.check_permission("read")
        sheet = frappe.get_doc(dict(
            doctype=SHEET, teacher=master.name, teacher_name_snapshot=master.teacher_name,
            from_date=first, to_date=last, status="待核对", currency="CNY",
            request_id=request_id, request_payload=_json(payload), input_json=_json(inputs),
            demo_batch=master.demo_batch,
        ))
        _set_prepared(sheet, _prepare(master.name, first, last, inputs, True))
        return _save(sheet)


@frappe.whitelist()
def update_sheet(sheet, lines):
    require_admin()
    inputs = _inputs(lines)
    with schedule_write():
        doc = _locked(SHEET, sheet)
        if doc.docstatus != 0:
            frappe.throw("已批准原账不能修改，请追加调整。")
        prepared = _prepare(doc.teacher, _date(doc.from_date), _date(doc.to_date), inputs, True)
        _set_prepared(doc, prepared)
        doc.input_json, doc.status, doc.check_fingerprint = _json(inputs), "待核对", None
        doc.checked_by = doc.checked_at = None
        return _save(doc)


@frappe.whitelist()
def check_sheet(sheet):
    require_admin()
    with schedule_write():
        doc = _locked(SHEET, sheet)
        if doc.docstatus != 0:
            frappe.throw("只能核对尚未批准的工资单。")
        prepared = _prepare(doc.teacher, _date(doc.from_date), _date(doc.to_date), _inputs(doc.input_json), True)
        issues = [line["review_issue"] for line in prepared[0] if line["review_issue"]]
        if issues:
            frappe.throw("工资单仍待核对：" + "；".join(issues))
        _assert_claims(doc, prepared[1])
        _set_prepared(doc, prepared, claim=True)
        doc.status, doc.check_fingerprint = "已核对", _digest(prepared)
        doc.checked_by, doc.checked_at = frappe.session.user, now_datetime()
        return _save(doc)


@frappe.whitelist()
def withdraw_sheet(sheet):
    require_admin()
    with schedule_write():
        doc = _locked(SHEET, sheet)
        if doc.docstatus != 0:
            frappe.throw("已批准工资不能撤回，请追加调整。")
        for row in doc.coverage:
            row.active_key = None
        doc.status, doc.check_fingerprint = "待核对", None
        doc.checked_by = doc.checked_at = None
        return _save(doc)


@frappe.whitelist()
def approve_sheet(sheet):
    require_finance()
    with schedule_write():
        doc = _locked(SHEET, sheet)
        if doc.docstatus == 1:
            return doc.name
        if doc.status != "已核对" or not doc.checked_by or not doc.check_fingerprint:
            frappe.throw("须先由行政核对工资来源、人数和覆盖范围。")
        prepared = _prepare(doc.teacher, _date(doc.from_date), _date(doc.to_date), _inputs(doc.input_json))
        if _digest(prepared) != doc.check_fingerprint:
            frappe.throw("来源、人数依据、规则或覆盖范围已变化，请行政重新核对。")
        _assert_claims(doc, prepared[1])
        doc.status, doc.approved_by, doc.approved_at = "已批准", frappe.session.user, now_datetime()
        return _save(doc, submit=True)


def _source_event(hour, event_type, reversal=None):
    """Called only inside the M4-A source transaction and its scheduling mutex."""
    root = _source_root(hour)
    day = get_datetime(hour.actual_start).date() if hour.actual_start else None
    rows = frappe.db.sql(
        f"""SELECT DISTINCT c.parent, c.line_index FROM `tab{COVERAGE}` c
            INNER JOIN `tab{SHEET}` s ON s.name=c.parent AND s.docstatus=1
            WHERE c.active_key IS NOT NULL AND c.active_key!='' AND
              (c.source_root=%s OR (c.teacher=%s AND c.pay_date=%s AND c.active_key=%s))
            FOR UPDATE""", (root, hour.teacher, day, _claim("D", hour.teacher, str(day))), as_dict=True,
    )
    for row in rows:
        sheet = frappe.get_doc(SHEET, row.parent)
        event_key = _claim("E", reversal.name if reversal else hour.name, event_type, sheet.name, row.line_index)
        if frappe.db.sql(f"SELECT name FROM `tab{ADJUSTMENT}` WHERE source_event=%s FOR UPDATE", (event_key,)):
            continue
        line = sheet.lines[row.line_index - 1]
        kind = event_type
        if event_type != "撤销":
            kind = "补录" if line.kind == "固定" and line.scope == "全天" else "修订"
        exact_lesson = line.kind in {"课程", "特殊"} and line.hour_entry == hour.name
        suggested = -decimal_amount(line.amount) if event_type == "撤销" and exact_lesson else Decimal("0.00")
        adjustment = frappe.get_doc(dict(
            doctype=ADJUSTMENT, sheet=sheet.name, teacher=sheet.teacher, currency="CNY", line_index=row.line_index,
            source_event=event_key, event_type=kind, hour_entry=hour.name, source_root=root,
            source_snapshot=_json(dict(hour_entry=hour.name, source_root=root, execution=hour.execution,
                                       pay_date=str(day), event_type=kind,
                                       reversal=reversal.name if reversal else None)),
            reason=("授课来源撤销；请按原覆盖约定决定金额" if kind == "撤销"
                    else "批准后补录或修订授课；须重新核对覆盖约定，禁止重复计薪"),
            suggested_amount=suggested, demo_batch=sheet.demo_batch,
        ))
        _save(adjustment)


def on_hour_confirmed(hour):
    _source_event(hour, "修订")


def on_hour_reversed(original, reversal):
    # Use the immutable confirmation as identity, and the reversal as event identity.
    _source_event(original, "撤销", reversal)


@frappe.whitelist()
def request_pay_correction(sheet, amount, reason, basis, request_id,
                           line_index=None, related_adjustment=None):
    require_pay_read()
    if amount is None or amount == "":
        frappe.throw("更正申请须明确填写追加差额；原账不改写。")
    amount = decimal_amount(amount)
    reason = _text(reason, "更正原因", required=True)
    basis = _text(basis, "更正依据", required=True)
    related_adjustment = _text(related_adjustment, "原调整", 140)
    if bool(related_adjustment) == (line_index is not None):
        frappe.throw("请选择原结算明细或已批准调整中的一个更正目标。")
    line_index = _integer(line_index, "原明细行号") if line_index is not None else None
    payload = dict(sheet=sheet, amount=str(amount), reason=reason, basis=basis,
                   line_index=line_index, related_adjustment=related_adjustment)
    with schedule_write():
        request_id, prior = _new_request(ADJUSTMENT, request_id, payload)
        if prior:
            return prior.name
        doc = _locked(SHEET, sheet)
        if doc.docstatus != 1:
            frappe.throw("未批准工资请行政撤回后修订，只有已批准原账可以追加更正。")
        target = _correction_target(doc, line_index, related_adjustment)
        correction = frappe.get_doc(dict(
            doctype=ADJUSTMENT, sheet=doc.name, teacher=doc.teacher, currency="CNY",
            event_type="更正", source_event=_claim("C", doc.name, request_id),
            line_index=target["line_index"], related_adjustment=related_adjustment,
            source_snapshot=_json(target), target_fingerprint=_digest(target),
            reason=reason, basis=basis, suggested_amount=amount,
            requested_by=frappe.session.user, requested_at=now_datetime(),
            request_id=request_id, request_payload=_json(payload), demo_batch=doc.demo_batch,
        ))
        return _save(correction)


def _correction_target(sheet, line_index, related_adjustment):
    if related_adjustment:
        original = _locked(ADJUSTMENT, related_adjustment)
        if original.sheet != sheet.name or original.docstatus != 1 or original.decision == "驳回":
            frappe.throw("更正目标必须是本单已经批准的有效调整。")
        line_index = original.line_index
        target = dict(adjustment=original.name, amount=str(decimal_amount(original.amount)),
                      source_snapshot_digest=_digest(original.source_snapshot),
                      decision_reason=original.decision_reason)
    else:
        if not line_index or line_index > len(sheet.lines):
            frappe.throw("原结算明细行号不存在。")
        line = sheet.lines[line_index - 1]
        target = dict(line=line.name, amount=str(decimal_amount(line.amount)),
                      kind=line.kind, class_size=line.class_size, headcount_basis=line.headcount_basis,
                      source_snapshot=line.source_snapshot, description=line.description)
    approved = frappe.db.sql(
        f"SELECT name, amount, decision FROM `tab{ADJUSTMENT}` "
        "WHERE sheet=%s AND docstatus=1 ORDER BY name FOR UPDATE", (sheet.name,), as_dict=True,
    )
    return dict(sheet=sheet.name, line_index=line_index, target=target,
                approved_adjustments=[dict(name=a.name, amount=str(decimal_amount(a.amount)),
                                           decision=a.decision) for a in approved])


@frappe.whitelist()
def reject_pay_correction(adjustment, reason):
    require_finance()
    reason = _text(reason, "驳回更正申请原因", required=True)
    with schedule_write():
        doc = _locked(ADJUSTMENT, adjustment)
        _locked(SHEET, doc.sheet)
        if doc.event_type != "更正":
            frappe.throw("来源变更事项须由财务决定金额，不能作为更正申请驳回。")
        if doc.docstatus == 1:
            if doc.decision == "驳回" and doc.decision_reason == reason:
                return doc.name
            frappe.throw("该申请已有财务决定，不得改写。")
        doc.amount, doc.decision, doc.decision_reason = Decimal("0.00"), "驳回", reason
        doc.approved_by, doc.approved_at = frappe.session.user, now_datetime()
        return _save(doc, submit=True)


@frappe.whitelist()
def approve_adjustment(adjustment, amount, reason):
    require_finance()
    if amount is None or amount == "":
        frappe.throw("财务调整须明确填写金额；零调整请填0。")
    amount = decimal_amount(amount)
    reason = _text(reason, "财务调整决定原因", required=True)
    with schedule_write():
        doc = _locked(ADJUSTMENT, adjustment)
        original_sheet = _locked(SHEET, doc.sheet)
        if doc.docstatus == 1:
            if doc.decision == "驳回" or decimal_amount(doc.amount) != amount or doc.decision_reason != reason:
                frappe.throw("该调整已批准为不同内容，不得改写。")
            return doc.name
        if doc.event_type == "更正":
            target = _correction_target(original_sheet, doc.line_index, doc.related_adjustment)
            if _digest(target) != doc.target_fingerprint:
                frappe.throw("更正目标或已批准调整已变化，请驳回旧申请后重新核对并申请。")
        decimal_amount(_balance(original_sheet)["net_payable"] + amount)
        doc.amount, doc.decision, doc.decision_reason = amount, "批准", reason
        doc.approved_by, doc.approved_at = frappe.session.user, now_datetime()
        return _save(doc, submit=True)


def _balance(doc, lock=True):
    clause = " FOR UPDATE" if lock else ""
    adjustments = frappe.db.sql(
        f"""SELECT name, docstatus, event_type, reason, suggested_amount, amount, decision_reason,
                   hour_entry, source_root, approved_by, approved_at, line_index,
                   related_adjustment, basis, requested_by, requested_at, decision
            FROM `tab{ADJUSTMENT}` WHERE sheet=%s ORDER BY creation,name""" + clause,
        (doc.name,), as_dict=True,
    )
    payments = frappe.db.sql(
        f"""SELECT name, operation_type, amount, cash_effect, paid_at, payment_method,
                   reference_no, reason, reversal_of, handled_by, balance_version,
                   net_payable_snapshot, is_difference, basis
            FROM `tab{PAYMENT}` WHERE sheet=%s ORDER BY creation,name""" + clause,
        (doc.name,), as_dict=True,
    )
    adjustment_amount = sum((decimal_amount(a.amount) for a in adjustments if a.docstatus == 1), Decimal("0.00"))
    net = decimal_amount(decimal_amount(doc.total_amount) + adjustment_amount)
    paid = decimal_amount(sum((decimal_amount(p.cash_effect) for p in payments), Decimal("0.00")))
    pending = any(a.docstatus == 0 for a in adjustments)
    reversed_names = {p.reversal_of for p in payments if p.operation_type == "冲正"}
    active = [p for p in payments if p.operation_type == "付款" and p.name not in reversed_names]
    outstanding = decimal_amount(net - paid)
    if doc.docstatus != 1:
        status = doc.status
    elif pending:
        status = "待处理调整"
    elif net < 0 or outstanding < 0 or (active and outstanding != 0):
        status = "待财务处理"
    elif outstanding == 0:
        status = "已付款" if active else "无需付款"
    else:
        status = "待付款"
    version = _digest(dict(sheet=doc.name, docstatus=doc.docstatus, fingerprint=doc.check_fingerprint,
                           total=str(decimal_amount(doc.total_amount)),
                           adjustments=[(a.name, a.docstatus, str(decimal_amount(a.amount)),
                                         a.decision, a.approved_at) for a in adjustments],
                           payments=[(p.name, str(decimal_amount(p.cash_effect))) for p in payments]))
    return dict(total_amount=decimal_amount(doc.total_amount), adjustment_amount=adjustment_amount,
                net_payable=net, net_paid=paid, outstanding=outstanding, status=status,
                payments=payments, adjustments=adjustments, balance_version=version)


@frappe.whitelist()
def get_pay_balance(sheet):
    require_pay_read()
    # Read-only summary: no scheduling mutex or business writes.
    return _balance(_locked(SHEET, sheet, lock=False), lock=False)


def _assert_events_resolved(doc):
    """Also detect missed source notifications before any new cash registration."""
    for line in doc.lines:
        sources = json.loads(line.source_snapshot or "[]")
        original_names = {s["hour_entry"] for s in sources}
        for source in sources:
            reversals = frappe.db.sql(
                f"SELECT name FROM `tab{HOUR}` WHERE reversal_of=%s FOR UPDATE", (source["hour_entry"],),
            )
            if reversals:
                original = frappe.get_doc(HOUR, source["hour_entry"])
                _source_event(original, "撤销", frappe.get_doc(HOUR, reversals[0][0]))
            else:
                # Raw source corruption is not a financial adjustment and must fail closed.
                current = _source(source["hour_entry"], doc.teacher)
                if current != source:
                    frappe.throw("原工资来源快照发生异常变化，暂停付款，请核查来源。")
        if line.kind == "固定" and line.scope == "全天":
            for name in set(_day_hours(doc.teacher, _date(line.pay_date))) - original_names:
                _source_event(frappe.get_doc(HOUR, name), "修订")


def _active_payment(balance):
    reversed_names = {p.reversal_of for p in balance["payments"] if p.operation_type == "冲正"}
    return next((p for p in balance["payments"] if p.operation_type == "付款" and p.name not in reversed_names), None)


def _paid_at(value):
    try:
        if not value:
            raise ValueError
        value = get_datetime(value)
        if value.tzinfo or value > now_datetime():
            raise ValueError
        return value
    except (ValueError, TypeError, OverflowError):
        frappe.throw("实际付款时间须按站点时区填写，且不能在未来。")


@frappe.whitelist()
def record_pay_payment(sheet, paid_at, payment_method, reference_no, request_id,
                       actual_amount=None, balance_version=None):
    return _record_payment(sheet, paid_at, payment_method, reference_no, request_id,
                           actual_amount, balance_version)


@frappe.whitelist()
def record_pay_difference(sheet, paid_at, payment_method, reference_no, request_id,
                          actual_amount, balance_version, reason, basis):
    return _record_payment(sheet, paid_at, payment_method, reference_no, request_id,
                           actual_amount, balance_version, difference=True, reason=reason, basis=basis)


def _record_payment(sheet, paid_at, payment_method, reference_no, request_id,
                    actual_amount, balance_version, *, difference=False, reason="", basis=""):
    require_finance()
    if actual_amount is None or actual_amount == "":
        frappe.throw("请明确确认实际线下付款金额，系统不会替换为当前应付金额。")
    actual_amount = decimal_amount(actual_amount)
    if actual_amount <= 0:
        frappe.throw("实际付款金额须大于零，不得生成虚假付款记录。")
    balance_version = _text(balance_version, "核算版本", 64, True)
    reason = _text(reason, "已发生付款差异原因", required=difference)
    basis = _text(basis, "已发生付款凭据依据", required=difference)
    paid_at = _paid_at(paid_at)
    if payment_method not in ("现金", "微信", "支付宝", "银行", "其他"):
        frappe.throw("请选择有效的线下付款方式。")
    reference_no = _text(reference_no, "线下付款凭据或参考号", 140, True)
    payload = dict(sheet=sheet, paid_at=str(paid_at), payment_method=payment_method, reference_no=reference_no,
                   actual_amount=str(actual_amount), balance_version=balance_version,
                   difference=difference, reason=reason, basis=basis)
    with schedule_write():
        request_id, prior = _new_request(PAYMENT, request_id, payload)
        if prior:
            return prior.name
        doc = _locked(SHEET, sheet)
        if doc.docstatus != 1:
            frappe.throw("只能登记已批准工资的实际付款。")
        if not difference:
            _assert_events_resolved(doc)
        balance = _balance(doc)
        if balance_version != balance["balance_version"]:
            frappe.throw("核算版本已变化，登记未保存。请重新核对；已经实际付款请用已发生付款差异登记保留真实金额。")
        if not difference and balance["status"] == "待处理调整":
            frappe.throw("工资来源有待处理调整，财务批准更正后才能付款。")
        if not difference and balance["net_payable"] <= 0:
            frappe.throw("净应付为零或负数，不得生成付款记录；负数须另行财务处理。")
        if not difference and actual_amount != balance["net_payable"]:
            frappe.throw("实际付款金额与本次整单净应付不一致；已发生付款须核对后通过差异登记，不会自动替换金额。")
        if difference and actual_amount == balance["net_payable"] and balance["status"] != "待处理调整":
            frappe.throw("实际金额与净应付一致且无待处理事项，请使用普通整单付款登记。")
        if _active_payment(balance):
            frappe.throw("该工资单已有有效整单付款，不得重复登记；登记有误须先冲正。")
        cycle = next((p.name for p in reversed(balance["payments"]) if p.operation_type == "冲正"), "initial")
        payment = frappe.get_doc(dict(
            doctype=PAYMENT, sheet=doc.name, teacher=doc.teacher, currency="CNY", operation_type="付款",
            amount=actual_amount, cash_effect=actual_amount, paid_at=paid_at,
            payment_method=payment_method, reference_no=reference_no, handled_by=frappe.session.user,
            balance_version=balance_version, net_payable_snapshot=balance["net_payable"],
            is_difference=int(difference), reason=reason, basis=basis,
            request_id=request_id, request_payload=_json(payload), active_key=_claim("P", doc.name, cycle),
            demo_batch=doc.demo_batch,
        ))
        return _save(payment)


@frappe.whitelist()
def reverse_pay_payment(payment, reason, request_id):
    require_finance()
    reason = _text(reason, "付款登记冲正原因", required=True)
    payload = dict(payment=payment, reason=reason)
    with schedule_write():
        request_id, prior = _new_request(PAYMENT, request_id, payload)
        if prior:
            return prior.name
        original = _locked(PAYMENT, payment)
        _locked(SHEET, original.sheet)
        if original.operation_type != "付款":
            frappe.throw("只能冲正原付款登记。")
        if frappe.db.sql(f"SELECT name FROM `tab{PAYMENT}` WHERE reversal_of=%s FOR UPDATE", (payment,)):
            frappe.throw("该付款已冲正，不能重复冲正。")
        reversal = frappe.get_doc(dict(
            doctype=PAYMENT, sheet=original.sheet, teacher=original.teacher, currency="CNY", operation_type="冲正",
            amount=decimal_amount(original.amount), cash_effect=-decimal_amount(original.cash_effect),
            paid_at=now_datetime(), payment_method=original.payment_method, reference_no=original.reference_no,
            reversal_of=original.name, reason=reason, handled_by=frappe.session.user,
            request_id=request_id, request_payload=_json(payload), demo_batch=original.demo_batch,
        ))
        return _save(reversal)


def _public_source(source):
    return {key: source[key] for key in (
        "hour_entry", "source_root", "teacher", "teacher_name_snapshot", "course_name_snapshot",
        "pay_date", "actual_start", "actual_end", "scheduled_start", "scheduled_end",
        "effect_minutes", "roster_size", "present_any", "issues",
    )} | {"name": source["hour_entry"]}


@frappe.whitelist()
def get_source_summary(hour_entry):
    require_pay_read()
    hour = frappe.get_doc(HOUR, _text(hour_entry, "授课来源", 140, True))
    session = frappe.get_doc("MX Session", hour.session)
    execution = frappe.get_doc("MX Session Execution", hour.execution)
    if is_admin():
        for doc in (hour, session, execution):
            doc.check_permission("read")
    reversal = frappe.db.get_value(HOUR, {"reversal_of": hour.name}, "name")
    valid = (hour.operation_type == "确认" and hour.effect_minutes > 0 and not reversal
             and session.docstatus == 1 and execution.docstatus == 1)
    if valid:
        return _public_source(_source(hour.name, hour.teacher, is_admin(), lock=False))
    return dict(name=hour.name, hour_entry=hour.name, source_root=_source_root(hour, lock=False),
                teacher=hour.teacher, teacher_name_snapshot=hour.teacher_name_snapshot,
                course_name_snapshot=hour.course_name_snapshot, actual_start=hour.actual_start,
                actual_end=hour.actual_end, scheduled_start=hour.scheduled_start,
                scheduled_end=hour.scheduled_end, effect_minutes=hour.effect_minutes,
                roster_size=len(session.students), reversal=reversal, is_valid=False,
                issues=["来源已撤销或未授课，不得作为正常计薪来源"])


@frappe.whitelist()
def list_pay_teachers():
    require_pay_read()
    if is_admin():
        return frappe.get_list("MX Teacher", fields=["name", "teacher_name"], limit_page_length=500)
    return frappe.db.sql("SELECT name, teacher_name FROM `tabMX Teacher` ORDER BY name LIMIT 500", as_dict=True)


@frappe.whitelist()
def list_pay_sources(teacher, from_date, to_date):
    require_pay_read()
    first, last = _date(from_date), _date(to_date)
    if first > last or (last - first).days > 366:
        frappe.throw("来源查询范围须顺序正确且不超过一年。")
    rows = frappe.db.sql(
        f"""SELECT h.name FROM `tab{HOUR}` h
            INNER JOIN `tabMX Session Execution` e ON e.name=h.execution AND e.docstatus=1
            INNER JOIN `tabMX Session` s ON s.name=h.session AND s.docstatus=1
            WHERE h.teacher=%s AND h.operation_type='确认' AND h.effect_minutes>0
              AND h.actual_start >= %s AND h.actual_start < %s
              AND NOT EXISTS (SELECT r.name FROM `tab{HOUR}` r WHERE r.reversal_of=h.name)
            ORDER BY h.actual_start,h.name LIMIT 501""", (teacher, first, last + timedelta(days=1)),
    )
    if len(rows) > 500:
        frappe.throw("授课来源超过500条，请缩小日期范围；不会截断来源清单。")
    return [_public_source(_source(r[0], teacher, is_admin(), lock=False)) for r in rows]


@frappe.whitelist()
def get_pay_report(filters=None):
    require_pay_read()
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except ValueError:
            frappe.throw("报表筛选条件格式不正确。")
    filters = filters or {}
    if not isinstance(filters, dict) or set(filters) - {"teacher", "from_date", "to_date", "status"}:
        frappe.throw("报表筛选条件不受支持。")
    query = {}
    if filters.get("teacher"):
        query["teacher"] = _text(filters["teacher"], "教师", 140, True)
    first = _date(filters["from_date"]) if filters.get("from_date") else None
    last = _date(filters["to_date"]) if filters.get("to_date") else None
    if first and last and first > last:
        frappe.throw("报表日期范围顺序不正确。")
    if first:
        query["to_date"] = [">=", first]
    if last:
        query["from_date"] = ["<=", last]
    names = frappe.get_list(SHEET, filters=query, pluck="name", order_by="creation desc", limit_page_length=1001)
    if len(names) > 1000:
        frappe.throw("报表超过1000张单，请缩小教师或日期范围；不会截断导出。")
    data = []
    for name in names:
        doc = frappe.get_doc(SHEET, name)
        doc.check_permission("read")
        balance = _balance(doc, lock=False)
        if filters.get("status") and filters["status"] != balance["status"]:
            continue
        first_row = True
        for line in doc.lines:
            day = _date(line.pay_date)
            if (first and day < first) or (last and day > last):
                continue
            data.append(dict(sheet=doc.name, teacher=doc.teacher_name_snapshot, pay_date=day, currency="CNY",
                             monetary_scope=f"整单 {doc.from_date} 至 {doc.to_date}；日期仅筛明细，付款不分摊",
                             kind=line.kind, description=line.description or line.hour_entry,
                             class_size=line.class_size or None, amount=line.amount, status=balance["status"],
                             net_payable=balance["net_payable"] if first_row else None,
                             net_paid=balance["net_paid"] if first_row else None,
                             outstanding=balance["outstanding"] if first_row else None))
            first_row = False
    columns = [
        {"fieldname": "sheet", "label": "工资结算单", "fieldtype": "Link", "options": SHEET, "width": 160},
        {"fieldname": "teacher", "label": "教师", "fieldtype": "Data", "width": 120},
        {"fieldname": "pay_date", "label": "业务日期", "fieldtype": "Date", "width": 100},
        {"fieldname": "kind", "label": "计费方式", "fieldtype": "Data", "width": 80},
        {"fieldname": "description", "label": "课次或覆盖说明", "fieldtype": "Data", "width": 200},
        {"fieldname": "class_size", "label": "计薪人数", "fieldtype": "Int", "width": 80},
        {"fieldname": "amount", "label": "原明细金额", "fieldtype": "Currency", "options": "currency", "width": 100},
        {"fieldname": "monetary_scope", "label": "整单金额所属期间（不分摊付款）", "fieldtype": "Data", "width": 340},
        {"fieldname": "status", "label": "结算状态", "fieldtype": "Data", "width": 120},
    ] + [{"fieldname": key, "label": "整单" + label + "（所选明细首行）", "fieldtype": "Currency", "options": "currency", "width": 210}
         for key, label in (("net_payable", "净应付"), ("net_paid", "已登记付款"), ("outstanding", "待处理差额"))]
    return columns, data
