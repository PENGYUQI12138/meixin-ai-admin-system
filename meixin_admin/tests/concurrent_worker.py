"""由受站点守卫保护的集成测试启动的独立连接进程，不是业务接口。"""

import json
import sys


def main():
    config = json.loads(sys.stdin.readline())
    if config.get("site") != "test_meixin_m1.localhost":
        raise RuntimeError("拒绝在非隔离站点启动并发测试。")
    import frappe
    from meixin_admin.scheduling import acquire_schedule_lock
    from meixin_admin.tests.run import assert_isolated_site

    frappe.init(site=config["site"], sites_path=config["sites_path"])
    frappe.connect()
    try:
        assert_isolated_site()
        frappe.flags.in_test = True
        frappe.set_user("Administrator")
        doctype = {
            "submit": "MX Session", "teacher": "MX Teacher", "room": "MX Room",
            "execution_submit": "MX Session Execution", "session_cancel": "MX Session",
            "settings": "MX Settings",
        }[config["operation"]]
        doc = frappe.get_single(doctype) if doctype == "MX Settings" else frappe.get_doc(doctype, config["name"])
        if doctype != "MX Settings" and (
            not doc.demo_batch or not doc.demo_batch.startswith(("TEST-M1-", "TEST-M2-"))
        ):
            raise RuntimeError("并发进程拒绝修改非本测试标记数据。")
        if config["hold"]:
            acquire_schedule_lock()
        print("LOCKED" if config["hold"] else "READY", flush=True)
        if sys.stdin.readline().strip() != "GO":
            raise RuntimeError("并发测试缺少执行信号。")
        print("ATTEMPT", flush=True)
        try:
            if config["operation"] in {"submit", "execution_submit"}:
                doc.submit()
            elif config["operation"] == "session_cancel":
                doc.cancel()
            elif config["operation"] == "settings":
                if config["field"] not in {
                    "present_rule", "leave_rule", "absent_rule", "other_rule", "session_cancel_rule",
                }:
                    raise RuntimeError("不支持的课消规则字段。")
                doc.set(config["field"], config["value"])
                doc.save()
            else:
                if config["field"] not in {"enabled", "capacity"}:
                    raise RuntimeError("不支持的并发测试字段。")
                doc.set(config["field"], config["value"])
                doc.save()
            frappe.db.commit()
            result = {"ok": True, "name": doc.name}
        except Exception as error:
            frappe.db.rollback()
            result = {"ok": False, "name": doc.name, "exception": type(error).__name__, "message": str(error)}
            if not isinstance(error, frappe.ValidationError):
                import traceback

                result["traceback"] = traceback.format_exc()
        print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        frappe.db.rollback()
        frappe.destroy()


if __name__ == "__main__":
    main()
