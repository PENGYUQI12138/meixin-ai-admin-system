"""Exercise the real CNY boundary without importing a Frappe site."""

import sys
import types
import unittest
from decimal import Decimal


frappe = types.ModuleType("frappe")


def reject(message):
    raise ValueError(message)


frappe.throw = reject
sys.modules["frappe"] = frappe

from meixin_admin.money import decimal_amount  # noqa: E402


class TestCNY(unittest.TestCase):
    def test_valid_cents(self):
        for value in ("0.01", "0.10", "1.23", "9999999999999.99"):
            with self.subTest(value=value):
                self.assertEqual(decimal_amount(value), Decimal(value))

    def test_rejects_precision_and_range(self):
        for value in ("1.000", "0.001", "10000000000000.00", "NaN"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    decimal_amount(value)
