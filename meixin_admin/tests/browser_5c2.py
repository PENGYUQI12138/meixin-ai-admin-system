"""Prepare 5C-2 UI scenarios on the disposable isolation site only."""

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
        frappe.throw("5C-2 隔离浏览器夹具状态异常，拒绝追加数据。")
    package = entitlements.create_student_package(
        records["students"][0], records["plans"][0], "购买", nowdate(),
        request_id=uuid.uuid4().hex,
    )
    frappe.db.set_value("MX Student Package", package, "demo_batch", BATCH)
    frappe.get_doc("MX Student Package", package).submit()
    payments.record_payment(package, "1.23", "现金", now_datetime(), uuid.uuid4().hex)
    entitlements.adjust_credits(package, -1, "5C-2 隔离夹具预先扣减", uuid.uuid4().hex)
    refund = payments.refund_close_package(package, "1.00", "5C-2 隔离夹具退款关闭", uuid.uuid4().hex)
    frappe.db.commit()
    return {"package": package, "refund": refund,
            "student": records["students"][0], "plan": records["plans"][0]}
