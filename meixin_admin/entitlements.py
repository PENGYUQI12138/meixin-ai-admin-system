"""M3 lesson-credit ledger primitives shared by package and M2 services."""
from contextlib import contextmanager

import frappe

CREDIT_ENTRY_DOCTYPE = "MX Lesson Credit Entry"


@contextmanager
def credit_ledger_write():
    """Allow only an in-process business service to create immutable entries."""
    previous = getattr(frappe.flags, "mx_credit_write", False)
    frappe.flags.mx_credit_write = True
    try:
        yield
    finally:
        frappe.flags.mx_credit_write = previous


def locked_credit_balance(student_package):
    """Return the authoritative balance while the caller holds schedule_write."""
    value = frappe.db.sql(
        f"SELECT COALESCE(SUM(effect), 0) FROM `tab{CREDIT_ENTRY_DOCTYPE}` WHERE student_package=%s",
        (student_package,),
    )[0][0]
    return int(value or 0)
