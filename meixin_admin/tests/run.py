"""绕过 Frappe 自动测试夹具的显式、严格站点守卫测试入口。"""

import unittest

import frappe
from frappe.utils import cint

TEST_SITE = "test_meixin_m1.localhost"


def assert_isolated_site():
    if (
        frappe.local.site != TEST_SITE
        or cint(frappe.conf.get("meixin_test_site")) != 1
        or cint(frappe.conf.get("allow_tests")) != 1
    ):
        raise RuntimeError(
            "拒绝运行测试：只能在 test_meixin_m1.localhost，且必须同时设置 meixin_test_site=1、allow_tests=1。"
        )
    if frappe.conf.get("db_type", "mariadb") != "mariadb":
        raise RuntimeError("本轮并发集成测试只验证现场同型 MariaDB 数据库。")


def run():
    """bench --site test_meixin_m1.localhost execute meixin_admin.tests.run.run"""
    assert_isolated_site()
    previous_user = frappe.session.user
    previous_in_test = frappe.flags.in_test
    try:
        frappe.flags.in_test = True
        frappe.set_user("Administrator")
        suite = unittest.TestSuite([
            unittest.defaultTestLoader.loadTestsFromName("meixin_admin.tests.test_m1"),
            unittest.defaultTestLoader.loadTestsFromName("meixin_admin.tests.test_m2"),
            unittest.defaultTestLoader.loadTestsFromName("meixin_admin.tests.test_m3"),
        ])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError(f"美心集成验收失败：失败 {len(result.failures)}，错误 {len(result.errors)}。")
        return {"site": TEST_SITE, "tests_run": result.testsRun, "failures": 0, "errors": 0, "skipped": len(result.skipped)}
    finally:
        frappe.db.rollback()
        frappe.flags.in_test = previous_in_test
        frappe.set_user(previous_user)
