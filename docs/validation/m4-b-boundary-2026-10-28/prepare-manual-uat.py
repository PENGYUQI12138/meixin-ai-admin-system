"""Create four independent fictional manual UAT cases; refuses to overwrite a prior batch."""
import json
import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import frappe
from meixin_admin import teacher_payroll as pay
from meixin_admin.tests.run import assert_isolated_site
from meixin_admin.tests.test_m4b import TestM4B


site = "test_meixin_m4b.localhost"
bench = Path("/home/frappe/frappe-bench")
config = json.loads((bench / "sites" / site / "site_config.json").read_text())
assert config.get("meixin_test_site") == 1 and config.get("allow_tests")
assert config.get("db_host") == "db" and os.environ["MEIXIN_TEST_SITE"] == site
os.chdir(bench / "sites")
frappe.init(site=site, sites_path=str(bench / "sites"))
frappe.connect()
try:
    assert_isolated_site()
    assert frappe.local.site == site and frappe.conf.db_host == "db"
    frappe.set_user("Administrator")
    batch = "TEST-M4B-MANUAL-UAT-20261028"
    assert not frappe.db.exists("MX Teacher", {"demo_batch": batch}), "Existing manual UAT must be preserved"
    fields = ("present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule")
    settings = frappe.get_single("MX Settings")
    before = {field: settings.get(field) for field in fields}
    for field in fields:
        settings.set(field, "不课消")
    settings.save()

    fixture = TestM4B()
    fixture.batch, fixture.next_slot, fixture.day = batch, 0, datetime(2026, 10, 26, 8)
    fixture.students = [fixture.master("MX Student", student_name=f"MANUAL-UAT虚构学生{i}",
                                       guardian_phone="00000000000") for i in range(5)]
    fixture.teacher = fixture.master("MX Teacher", teacher_name="TEST-M4B-MANUAL-UAT虚构教师")
    fixture.course = fixture.master("MX Course", course_name="MANUAL-UAT虚构课程", default_duration_minutes=90)
    fixture.room = fixture.master("MX Room", room_name="MANUAL-UAT虚构教室", capacity=8)
    first = fixture.lesson(4, 3, start=datetime(2026, 10, 26, 8))[2]
    second = fixture.lesson(5, 4, start=datetime(2026, 10, 26, 10))[2]
    wrong = fixture.lesson(5, 4, start=datetime(2026, 10, 27, 8))[2]
    drift_first = fixture.lesson(4, 3, start=datetime(2026, 10, 27, 12))[2]
    drift_second = fixture.lesson(5, 4, start=datetime(2026, 10, 27, 14))[2]

    def sheet(lines, expected, approved=True):
        name = pay.create_sheet(fixture.teacher.name, "2026-10-26", "2026-10-27", lines, uuid.uuid4().hex)
        if approved:
            pay.check_sheet(name)
            pay.approve_sheet(name)
        assert pay.get_pay_balance(name)["net_payable"] == Decimal(str(expected))
        return name

    normal = sheet([fixture.line(first, 4), fixture.line(second, 5)], 220, approved=False)
    headcount = sheet([fixture.line(wrong, 4, headcount_basis="虚构误核四人，完整五人名单用于人工追加20元更正")], 100)
    fixed = sheet([fixture.fixed(date="2026-10-27", amount=300, work_key=batch + "-work")], 300)
    drift = sheet([fixture.line(drift_first, 4), fixture.line(drift_second, 5)], 220)
    for name in (normal, headcount, fixed, drift):
        assert not frappe.db.exists("MX Teacher Pay Adjustment", {"sheet": name})
        assert not frappe.db.exists("MX Teacher Pay Payment", {"sheet": name})
    assert frappe.get_doc("MX Teacher Pay Sheet", normal).status == "待核对"
    for field, value in before.items():
        settings.set(field, value)
    settings.save()
    assert {field: settings.get(field) for field in fields} == before
    frappe.db.commit()
    print(json.dumps({"site": site, "batch": batch, "teacher": fixture.teacher.name,
                      "date_range": ["2026-10-26", "2026-10-27"],
                      "normal_pending_sheet": normal, "headcount_approved_sheet": headcount,
                      "fixed_approved_sheet": fixed, "drift_approved_unpaid_sheet": drift,
                      "hours": [first, second, wrong, drift_first, drift_second],
                      "initial_amounts": [220, 100, 300, 220], "settings_restored": True,
                      "existing_users_unchanged": True, "prior_uat_preserved": True}, ensure_ascii=False))
except BaseException:
    frappe.db.rollback()
    raise
finally:
    frappe.destroy()
