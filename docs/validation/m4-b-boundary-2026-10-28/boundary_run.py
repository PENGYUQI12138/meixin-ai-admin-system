"""One-off guarded boundary verification; no production or schema operations."""
import json
import time
import unittest
from pathlib import Path

import frappe


def run(mode="boundary", output="boundary"):
    from drill import guard, snapshot

    guard()
    assert mode in {"boundary", "payroll"}
    before = snapshot()
    settings = frappe.db.sql("SELECT field,value FROM tabSingles WHERE doctype='MX Settings'", as_dict=True)
    previous_in_test = frappe.flags.in_test
    frappe.flags.in_test = True
    names = (["meixin_admin.tests.test_m4b", "meixin_admin.tests.test_m4b_review"]
             if mode == "payroll" else [
                 "meixin_admin.tests.test_m4b_review.TestM4BReview.test_26_negative_net_rejects_difference_cash_that_would_overflow_balance_or_report",
                 "meixin_admin.tests.test_m4b_review.TestM4BReview.test_27_paid_correction_rejects_overflow_without_leaving_an_approval"])
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
    started = time.monotonic()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        frappe.db.rollback()
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM tabSingles WHERE doctype='MX Settings'")
        for row in settings:
            frappe.db.sql("INSERT INTO tabSingles (doctype,field,value) VALUES ('MX Settings',%s,%s)",
                          (row.field, row.value))
        frappe.db.commit()
        frappe.clear_document_cache("MX Settings", "MX Settings")
        frappe.flags.in_test = previous_in_test
    after = snapshot()
    summary = dict(date="2026-10-28", site=frappe.local.site, mode=mode,
                   scope="real Frappe whitelist dispatcher and MariaDB transactions; not network HTTP",
                   tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), duration_seconds=round(time.monotonic() - started, 3),
                   old_history_unchanged=before == after, before=before, after=after)
    evidence = Path("/home/frappe/frappe-bench/sites/m4b-upgrade-evidence/boundary-20261028")
    evidence.mkdir(exist_ok=True)
    (evidence / (output + ".json")).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key not in {"before", "after"}}, ensure_ascii=False))
    assert before == after, "Preserved fictional history changed"
    assert result.wasSuccessful(), "Boundary/payroll verification failed; see actual test log"
    return {key: value for key, value in summary.items() if key not in {"before", "after"}}
