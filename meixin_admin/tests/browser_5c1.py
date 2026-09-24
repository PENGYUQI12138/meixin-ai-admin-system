"""Prepare 5C-1 correction scenarios on the disposable isolation site only."""

import uuid

import frappe
from frappe.utils import nowdate, now_datetime

from meixin_admin import entitlements, payments
from meixin_admin.tests.browser_5b import BATCH, inspect
from meixin_admin.tests.run import assert_isolated_site


def prepare():
    assert_isolated_site()
    frappe.set_user("Administrator")
    records = inspect()
    if len(records["plans"]) != 1 or records["packages"]:
        frappe.throw("5C-1 隔离浏览器夹具状态异常，拒绝追加数据。")
    student = records["students"][0]
    plan = records["plans"][0]
    result = {}
    for label in ("reversal", "refund", "zero"):
        name = entitlements.create_student_package(
            student, plan, "购买", nowdate(), request_id=uuid.uuid4().hex,
        )
        frappe.db.set_value("MX Student Package", name, "demo_batch", BATCH)
        frappe.get_doc("MX Student Package", name).submit()
        receipt = payments.record_payment(name, "1.23", "现金", now_datetime(), uuid.uuid4().hex)
        result[label] = {"package": name, "receipt": receipt}
    entitlements.adjust_credits(result["zero"]["package"], -20, "5C-1 隔离零余额夹具", uuid.uuid4().hex)
    frappe.db.commit()
    return result
