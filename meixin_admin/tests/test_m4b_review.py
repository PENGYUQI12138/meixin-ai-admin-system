"""Work review regressions: real Frappe dispatcher, MariaDB and commit boundaries."""
import json
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal

import frappe
from frappe.utils import now_datetime
from werkzeug.test import EnvironBuilder

from meixin_admin import teacher_payroll as pay
from meixin_admin.tests import test_m4b


class TestM4BReview(unittest.TestCase):
    def setUp(self):
        self.settings = frappe.db.sql(
            "SELECT field,value FROM tabSingles WHERE doctype='MX Settings'", as_dict=True
        )
        self.fixture = test_m4b.TestM4B()
        self.fixture.setUp()
        self.finance = self.fixture.user("Meixin Finance")
        self.manager = self.fixture.user("Meixin Manager")
        self.scheduler = self.fixture.user("Meixin Scheduler")
        self.outsider = self.fixture.user()
        self.system_manager = self.fixture.user("System Manager")
        self.original_request = getattr(frappe.local, "request", None)
        frappe.local.request = EnvironBuilder(method="POST").get_request()

    def tearDown(self):
        try:
            committed = self.fixture.committed
            self.fixture.tearDown()
            if committed:
                frappe.set_user("Administrator")
                frappe.db.sql("DELETE FROM tabSingles WHERE doctype='MX Settings'")
                for row in self.settings:
                    frappe.db.sql(
                        "INSERT INTO tabSingles (doctype,field,value) VALUES ('MX Settings',%s,%s)",
                        (row.field, row.value),
                    )
                frappe.db.commit()
                frappe.clear_document_cache("MX Settings", "MX Settings")
        finally:
            frappe.local.request = self.original_request

    def call(self, method, **args):
        # Exercise Frappe's real whitelist/HTTP-method dispatcher without mocking a service.
        from frappe.handler import execute_cmd

        original = frappe.local.form_dict
        try:
            frappe.local.form_dict = frappe._dict(args)
            return execute_cmd("meixin_admin.teacher_payroll." + method)
        finally:
            frappe.local.form_dict = original

    def balance(self, sheet):
        return self.call("get_pay_balance", sheet=sheet)

    def cash_args(self, sheet):
        balance = self.balance(sheet)
        return dict(sheet=sheet, paid_at=str(now_datetime()), payment_method="现金",
                    reference_no="TEST真实接口虚构付款", request_id=uuid.uuid4().hex,
                    actual_amount=str(balance["net_payable"]),
                    balance_version=balance["balance_version"])

    def correction(self, sheet, amount, *, line_index=1, related_adjustment=None,
                   reason="虚构财务纠错", basis="已核对原约定及虚构凭据", request_id=None):
        args = dict(sheet=sheet, amount=amount, reason=reason, basis=basis,
                    request_id=request_id or uuid.uuid4().hex)
        if related_adjustment:
            args["related_adjustment"] = related_adjustment
        elif line_index is not None:
            args["line_index"] = line_index
        return self.call("request_pay_correction", **args)

    def approve(self, adjustment, amount):
        return self.call("approve_adjustment", adjustment=adjustment, amount=amount,
                         reason="已核对依据，财务批准追加差额")

    def two_lessons(self, *, cross_date=False):
        first = self.fixture.lesson(4, 3)[2]
        second = self.fixture.lesson(5, 4, start=(self.fixture.day + timedelta(days=1))
                                     if cross_date else None)[2]
        sheet = self.fixture.approved([self.fixture.line(first), self.fixture.line(second, 5)])
        return sheet

    def test_01_stale_window_cannot_replace_220_actual_cash_with_100_and_difference_is_preserved(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        old_window = self.cash_args(sheet)
        self.fixture.commit()
        adjustment = self.correction(sheet, -120, line_index=2)
        self.approve(adjustment, -120)
        frappe.db.commit()
        with self.assertRaises(frappe.ValidationError):
            self.call("record_pay_payment", **old_window)
        frappe.db.rollback()
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 0)
        self.assertEqual(self.balance(sheet)["net_payable"], Decimal("100.00"))
        args = self.cash_args(sheet)
        args.update(actual_amount="220.00", reason="已发生的220元付款迟登记",
                    basis="虚构现金交接凭据确认220元；核算后改为100元")
        payment = self.call("record_pay_difference", **args)
        frappe.db.commit()
        stored = frappe.get_doc(pay.PAYMENT, payment)
        self.assertEqual(Decimal(str(stored.amount)), 220)
        self.assertEqual(Decimal(str(stored.cash_effect)), 220)
        self.assertEqual(self.balance(sheet)["net_paid"], Decimal("220.00"))
        self.assertEqual(self.balance(sheet)["outstanding"], Decimal("-120.00"))
        self.assertEqual(self.balance(sheet)["status"], "待财务处理")
        self.assertEqual(self.call("record_pay_difference", **args), payment)
        with self.assertRaises(frappe.ValidationError):
            self.call("record_pay_payment", **self.cash_args(sheet))

    def test_02_missing_actual_confirmation_or_version_never_creates_cash(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        frappe.set_user(self.finance)
        for field in ("actual_amount", "balance_version"):
            for empty in (None, ""):
                args = self.cash_args(sheet)
                args[field] = empty
                with self.subTest(field=field, empty=empty), self.assertRaises(frappe.ValidationError):
                    self.call("record_pay_payment", **args)
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 0)

    def test_03_current_version_still_requires_exact_whole_sheet_actual_amount(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        for amount in (100, 120, 219, 221, 0, -1, "220.001", True):
            args = self.cash_args(sheet)
            args["actual_amount"] = amount
            with self.subTest(amount=amount), self.assertRaises(frappe.ValidationError):
                self.call("record_pay_payment", **args)
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 0)
        payment = self.call("record_pay_payment", **self.cash_args(sheet))
        self.assertEqual(Decimal(str(frappe.db.get_value(pay.PAYMENT, payment, "amount"))), 220)

    def test_04_idempotency_compares_actual_amount_and_version_even_after_cash_is_recorded(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        args = self.cash_args(sheet)
        payment = self.call("record_pay_payment", **args)
        self.assertEqual(self.call("record_pay_payment", **args), payment)
        for field, value in (("actual_amount", 100), ("balance_version", "0" * 64)):
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                self.call("record_pay_payment", **(args | {field: value}))
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 1)

    def test_05_same_net_amount_after_two_corrections_does_not_restore_old_version(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        old_window = self.cash_args(sheet)
        first = self.correction(sheet, -120, line_index=2)
        self.approve(first, -120)
        second = self.correction(sheet, 120, related_adjustment=first)
        self.approve(second, 120)
        self.assertEqual(self.balance(sheet)["net_payable"], 220)
        self.assertNotEqual(self.balance(sheet)["balance_version"], old_window["balance_version"])
        with self.assertRaises(frappe.ValidationError):
            self.call("record_pay_payment", **old_window)

    def test_06_difference_is_controlled_not_an_alternative_partial_payment_route(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        for field in ("reason", "basis"):
            args = self.cash_args(sheet)
            args.update(actual_amount=100, reason="虚构已发生差异", basis="虚构付款凭据")
            args[field] = ""
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                self.call("record_pay_difference", **args)
        args = self.cash_args(sheet)
        args.update(actual_amount=100, reason="误付100元已发生", basis="虚构银行回单100元")
        first = self.call("record_pay_difference", **args)
        self.assertEqual(self.balance(sheet)["net_paid"], 100)
        self.assertEqual(self.balance(sheet)["outstanding"], 120)
        self.assertEqual(self.balance(sheet)["status"], "待财务处理")
        args["request_id"] = uuid.uuid4().hex
        args["balance_version"] = self.balance(sheet)["balance_version"]
        with self.assertRaises(frappe.ValidationError):
            self.call("record_pay_difference", **args)
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 1)
        self.assertEqual(Decimal(str(frappe.db.get_value(pay.PAYMENT, first, "amount"))), 100)

    def test_07_difference_preserves_already_paid_cash_with_pending_zero_or_negative_payable(self):
        for amount in (None, -100, -150):
            with self.subTest(correction=amount):
                frappe.set_user("Administrator")
                sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
                frappe.set_user(self.finance)
                pending = self.correction(sheet, -100 if amount is None else amount)
                if amount is not None:
                    self.approve(pending, amount)
                args = self.cash_args(sheet)
                args.update(actual_amount=100, reason="核算变化前已付100元",
                            basis="虚构原付款凭据，不自动扣款或退款")
                payment = self.call("record_pay_difference", **args)
                self.assertEqual(Decimal(str(frappe.db.get_value(pay.PAYMENT, payment, "cash_effect"))), 100)
                balance = self.balance(sheet)
                self.assertEqual(balance["net_paid"], 100)
                self.assertEqual(balance["outstanding"], balance["net_payable"] - 100)
                self.assertEqual(balance["status"], "待处理调整" if amount is None else "待财务处理")

    def test_08_five_students_mischecked_four_adds_20_without_cancelling_source_or_releasing_claims(self):
        _, execution, hour = self.fixture.lesson(5, 4)
        sheet = self.fixture.approved([self.fixture.line(hour, 4)])
        before = frappe.get_doc(pay.SHEET, sheet).as_json()
        claims = frappe.db.sql(
            f"SELECT * FROM `tab{pay.COVERAGE}` WHERE parent=%s ORDER BY name", (sheet,), as_dict=True
        )
        frappe.set_user(self.manager)
        correction = self.correction(sheet, 20, reason="5人误核为4人", basis="冻结名单5人，含1名请假")
        with self.assertRaises(frappe.PermissionError):
            self.approve(correction, 20)
        frappe.set_user(self.finance)
        self.approve(correction, 20)
        self.assertEqual(self.balance(sheet)["net_payable"], 120)
        self.assertEqual(frappe.get_doc(pay.SHEET, sheet).as_json(), before)
        self.assertEqual(frappe.db.get_value("MX Session Execution", execution.name, "docstatus"), 1)
        self.assertEqual(frappe.db.count(pay.HOUR, {"reversal_of": hour}), 0)
        self.assertEqual(frappe.db.sql(f"SELECT * FROM `tab{pay.COVERAGE}` WHERE parent=%s ORDER BY name",
                                     (sheet,), as_dict=True), claims)
        frappe.set_user("Administrator")
        duplicate = self.fixture.sheet([self.fixture.line(hour, 5)])
        with self.assertRaises(frappe.ValidationError):
            pay.check_sheet(duplicate)

    def test_09_independent_fixed_work_fee_is_corrected_by_delta_and_work_claim_stays_reserved(self):
        key = "独立工作-" + self.fixture.batch
        sheet = self.fixture.approved([self.fixture.fixed(work_key=key, amount=300)])
        before = frappe.get_doc(pay.SHEET, sheet).as_json()
        frappe.set_user(self.finance)
        correction = self.correction(sheet, -120, reason="独立备课费误填300",
                                     basis="虚构工作约定为180元")
        self.approve(correction, -120)
        self.assertEqual(self.balance(sheet)["net_payable"], 180)
        self.assertEqual(frappe.get_doc(pay.SHEET, sheet).as_json(), before)
        frappe.set_user("Administrator")
        duplicate = self.fixture.sheet([self.fixture.fixed(work_key=key, amount=180)])
        with self.assertRaises(frappe.ValidationError):
            pay.check_sheet(duplicate)

    def test_10_wrong_approved_adjustment_is_corrected_by_linked_delta_without_overwrite(self):
        sheet = self.fixture.approved([self.fixture.fixed(work_key=self.fixture.batch, amount=300)])
        frappe.set_user(self.finance)
        first = self.correction(sheet, -20, basis="虚构错误决定：先批准减少20")
        self.approve(first, -20)
        original = frappe.get_doc(pay.ADJUSTMENT, first).as_json()
        second = self.correction(sheet, -100, related_adjustment=first,
                                 reason="前一批准调整误填", basis="约定应合计减120，追加减100")
        self.approve(second, -100)
        self.assertEqual(frappe.get_doc(pay.ADJUSTMENT, second).related_adjustment, first)
        self.assertEqual(frappe.get_doc(pay.ADJUSTMENT, first).as_json(), original)
        self.assertEqual(self.balance(sheet)["net_payable"], 180)

    def test_11_correction_request_idempotency_includes_target_amount_reason_and_basis(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        frappe.set_user(self.finance)
        args = dict(sheet=sheet, amount=20, reason="虚构人数纠错", basis="虚构名单依据",
                    request_id=uuid.uuid4().hex, line_index=1)
        first = self.call("request_pay_correction", **args)
        self.assertEqual(self.call("request_pay_correction", **args), first)
        for field, value in (("amount", 30), ("reason", "不同原因"), ("basis", "不同依据"), ("line_index", 2)):
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                self.call("request_pay_correction", **(args | {field: value}))
        self.assertEqual(frappe.db.count(pay.ADJUSTMENT, {"sheet": sheet}), 1)

    def test_12_missing_invalid_or_cross_sheet_correction_targets_are_rejected(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        second = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        frappe.set_user(self.finance)
        other = self.correction(second, 20)
        self.approve(other, 20)
        common = dict(sheet=sheet, amount=20, reason="虚构更正", basis="虚构依据")
        cases = [{}, {"line_index": 0}, {"line_index": 2}, {"line_index": True},
                 {"line_index": "1.5"}, {"related_adjustment": other},
                 {"line_index": 1, "related_adjustment": other},
                 {"line_index": 1, "basis": ""}, {"line_index": 1, "reason": ""}]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(frappe.ValidationError):
                self.call("request_pay_correction", **(common | fields | {"request_id": uuid.uuid4().hex}))
        self.assertEqual(frappe.db.count(pay.ADJUSTMENT, {"sheet": sheet}), 0)

    def test_13_unapproved_sheet_or_unapproved_adjustment_cannot_be_financially_corrected(self):
        sheet = self.fixture.sheet([self.fixture.line(self.fixture.lesson()[2])])
        with self.assertRaises(frappe.ValidationError):
            self.correction(sheet, 20)
        pay.check_sheet(sheet)
        pay.approve_sheet(sheet)
        frappe.set_user(self.finance)
        pending = self.correction(sheet, 20)
        with self.assertRaises(frappe.ValidationError):
            self.correction(sheet, -20, related_adjustment=pending)

    def test_14_only_finance_can_approve_reject_or_register_a_cash_difference(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        frappe.set_user(self.finance)
        pending = self.correction(sheet, 20)
        for user in (self.manager, self.scheduler, self.outsider, self.system_manager, "Guest"):
            frappe.set_user(user)
            with self.subTest(user=user):
                with self.assertRaises(frappe.PermissionError):
                    self.approve(pending, 20)
                with self.assertRaises(frappe.PermissionError):
                    self.call("reject_pay_correction", adjustment=pending, reason="无财务权限")
                with self.assertRaises(frappe.PermissionError):
                    self.call("record_pay_difference", sheet=sheet, paid_at=str(now_datetime()),
                              payment_method="现金", reference_no="虚构凭据", request_id=uuid.uuid4().hex,
                              actual_amount=100, balance_version="0" * 64, reason="无财务权限", basis="虚构依据")
        for user in (self.outsider, self.system_manager, "Guest"):
            frappe.set_user(user)
            with self.subTest(request_user=user), self.assertRaises(frappe.PermissionError):
                self.correction(sheet, 20)

    def test_15_rejection_is_audited_closes_pending_and_does_not_change_old_cash_or_claims(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        frappe.set_user(self.finance)
        pending = self.correction(sheet, 20)
        self.assertEqual(self.balance(sheet)["status"], "待处理调整")
        self.call("reject_pay_correction", adjustment=pending, reason="依据错误，财务驳回")
        rejected = frappe.get_doc(pay.ADJUSTMENT, pending)
        self.assertEqual(rejected.docstatus, 1)
        self.assertEqual(rejected.decision, "驳回")
        self.assertEqual(Decimal(str(rejected.amount)), 0)
        self.assertEqual(self.balance(sheet)["net_payable"], 100)
        self.assertEqual(self.balance(sheet)["status"], "待付款")
        self.assertEqual(self.call("reject_pay_correction", adjustment=pending,
                                   reason="依据错误，财务驳回"), pending)
        with self.assertRaises(frappe.ValidationError):
            self.approve(pending, 20)
        with self.assertRaises(frappe.ValidationError):
            self.correction(sheet, 20, related_adjustment=pending)

    def test_16_paid_correction_preserves_cash_and_shows_difference_without_automatic_collection(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        frappe.set_user(self.finance)
        payment = self.call("record_pay_payment", **self.cash_args(sheet))
        before = frappe.get_doc(pay.PAYMENT, payment).as_json()
        correction = self.correction(sheet, 20)
        self.approve(correction, 20)
        self.assertEqual(self.balance(sheet)["net_payable"], 120)
        self.assertEqual(self.balance(sheet)["net_paid"], 100)
        self.assertEqual(self.balance(sheet)["outstanding"], 20)
        self.assertEqual(self.balance(sheet)["status"], "待财务处理")
        self.assertEqual(frappe.get_doc(pay.PAYMENT, payment).as_json(), before)
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 1)

    def test_17_stale_duplicate_target_corrections_cannot_both_be_approved_in_concurrent_transactions(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson(5, 4)[2], 4)])
        frappe.set_user(self.finance)
        first, second = self.correction(sheet, 20), self.correction(sheet, 20)
        args = lambda name: dict(adjustment=name, amount=20, reason="虚构并发批准人数更正")
        results = self.fixture.race("pay_adjustment", first, args(first), "pay_adjustment", second, args(second))
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        frappe.set_user(self.finance)
        self.assertEqual(self.balance(sheet)["net_payable"], 120)
        self.assertEqual(frappe.db.get_value(pay.ADJUSTMENT, second, "docstatus"), 0)
        self.call("reject_pay_correction", adjustment=second, reason="另一申请先获批，本申请已过期")

    def test_18_adjustment_winning_transaction_makes_the_old_payment_version_fail_closed(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        old_window = self.cash_args(sheet)
        adjustment = self.correction(sheet, -120, line_index=2)
        results = self.fixture.race(
            "pay_adjustment", adjustment, dict(adjustment=adjustment, amount=-120, reason="先核准减少120"),
            "pay_payment", sheet, old_window,
        )
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 0)
        frappe.set_user(self.finance)
        self.assertEqual(self.balance(sheet)["net_payable"], 100)

    def test_19_partial_date_report_explicitly_labels_whole_sheet_amounts_and_does_not_allocate_cash(self):
        sheet = self.two_lessons(cross_date=True)
        frappe.set_user(self.finance)
        self.call("record_pay_payment", **self.cash_args(sheet))
        columns, rows = self.call("get_pay_report", filters={
            "teacher": self.fixture.teacher.name, "from_date": "2020-01-15", "to_date": "2020-01-15"
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(str(rows[0]["amount"])), 100)
        self.assertEqual(Decimal(str(rows[0]["net_payable"])), 220)
        self.assertEqual(Decimal(str(rows[0]["net_paid"])), 220)
        self.assertEqual(Decimal(str(rows[0]["outstanding"])), 0)
        self.assertIn("整单", rows[0]["monetary_scope"])
        for key in ("net_payable", "net_paid", "outstanding"):
            self.assertIn("整单", next(column["label"] for column in columns if column["fieldname"] == key))

    def test_20_partial_date_csv_export_keeps_explicit_scope_and_full_cash_fact(self):
        from frappe.desk import query_report

        sheet = self.two_lessons(cross_date=True)
        frappe.set_user(self.finance)
        self.call("record_pay_payment", **self.cash_args(sheet))
        original_form, original_response = frappe.local.form_dict, frappe.local.response
        try:
            frappe.local.form_dict = frappe._dict(report_name="教师工资结算概览", file_format_type="CSV",
                filters=json.dumps(dict(teacher=self.fixture.teacher.name, from_date="2020-01-15", to_date="2020-01-15")))
            frappe.local.response = frappe._dict()
            query_report.export_query()
            content = frappe.local.response.filecontent.decode("utf-8-sig")
            self.assertIn("整单", content)
            self.assertIn(sheet, content)
            self.assertIn("220", content)
            self.assertNotIn("2020-01-16,课程", content)
        finally:
            frappe.local.form_dict, frappe.local.response = original_form, original_response

    def test_21_finance_cannot_reject_a_real_source_event_to_unblock_cash(self):
        _, execution, hour = self.fixture.lesson()
        sheet = self.fixture.approved([self.fixture.line(hour)])
        execution.cancel()
        event = self.fixture.adjustments(sheet)[0].name
        before = frappe.get_doc(pay.ADJUSTMENT, event).as_json()
        frappe.set_user(self.finance)
        with self.assertRaises(frappe.ValidationError):
            self.call("reject_pay_correction", adjustment=event, reason="不可驳回真实撤销事件")
        self.assertEqual(frappe.get_doc(pay.ADJUSTMENT, event).as_json(), before)
        self.assertEqual(self.balance(sheet)["status"], "待处理调整")

    def test_22_current_version_is_checked_for_difference_registration_too(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        args = self.cash_args(sheet)
        args.update(actual_amount=100, reason="旧窗口登记已发生100元", basis="虚构现金凭据")
        correction = self.correction(sheet, -120, line_index=2)
        self.approve(correction, -120)
        with self.assertRaises(frappe.ValidationError):
            self.call("record_pay_difference", **args)
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 0)

    def test_23_difference_idempotency_keeps_actual_cash_reason_and_basis_immutable(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        args = self.cash_args(sheet)
        args.update(actual_amount=200, reason="已发生误付200元", basis="虚构银行回单200元")
        payment = self.call("record_pay_difference", **args)
        original = frappe.get_doc(pay.PAYMENT, payment).as_json()
        self.assertEqual(self.call("record_pay_difference", **args), payment)
        for field, value in (("actual_amount", 100), ("balance_version", "0" * 64),
                             ("reason", "不同原因"), ("basis", "不同凭据")):
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                self.call("record_pay_difference", **(args | {field: value}))
        self.assertEqual(frappe.get_doc(pay.PAYMENT, payment).as_json(), original)
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 1)

    def test_24_payment_winning_before_correction_request_keeps_original_220_cash(self):
        sheet = self.two_lessons()
        frappe.set_user(self.finance)
        payment_args = self.cash_args(sheet)
        correction_args = dict(sheet=sheet, amount=-120, line_index=2,
                               reason="另一事务后提交更正", basis="虚构核对依据", request_id=uuid.uuid4().hex)
        results = self.fixture.race("pay_payment", sheet, payment_args,
                                    "pay_correction", sheet, correction_args)
        self.assertTrue(results[0]["ok"])
        self.assertTrue(results[1]["ok"])
        frappe.set_user(self.finance)
        self.approve(results[1]["name"], -120)
        self.assertEqual(self.balance(sheet)["net_paid"], 220)
        self.assertEqual(self.balance(sheet)["net_payable"], 100)
        self.assertEqual(self.balance(sheet)["outstanding"], -120)
        self.assertEqual(self.balance(sheet)["status"], "待财务处理")
        self.assertEqual(frappe.db.count(pay.PAYMENT, {"sheet": sheet}), 1)

    def test_25_deep_linked_corrections_keep_each_prior_fact_without_exponential_snapshots(self):
        sheet = self.fixture.approved([self.fixture.line(self.fixture.lesson()[2])])
        original_sheet = frappe.get_doc(pay.SHEET, sheet).as_json()
        frappe.set_user(self.finance)
        previous, originals, lengths = None, {}, []
        for index in range(16):
            correction = self.correction(sheet, 1, related_adjustment=previous,
                                         reason=f"虚构连续更正第{index + 1}次", basis="每次追加1元，保留前一批准事实")
            self.approve(correction, 1)
            doc = frappe.get_doc(pay.ADJUSTMENT, correction)
            self.assertEqual(doc.related_adjustment or None, previous)
            self.assertLess(len(doc.source_snapshot), 20000)
            lengths.append(len(doc.source_snapshot))
            originals[correction] = doc.as_json()
            previous = correction
        self.assertEqual(self.balance(sheet)["net_payable"], 116)
        self.assertLess(max(lengths), 4 * min(lengths) + 3000)
        self.assertEqual(frappe.get_doc(pay.SHEET, sheet).as_json(), original_sheet)
        for name, original in originals.items():
            self.assertEqual(frappe.get_doc(pay.ADJUSTMENT, name).as_json(), original)
