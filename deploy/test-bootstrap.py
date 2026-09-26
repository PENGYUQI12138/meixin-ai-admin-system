"""Bootstrap only the independent, disposable M1 test site; never a pilot site."""

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time

BENCH = Path("/home/frappe/frappe-bench")
SITE = os.environ.get("MEIXIN_TEST_SITE", "test_meixin_m1.localhost")
if SITE not in {"test_meixin_m1.localhost", "test_meixin_m4.localhost"}:
    raise RuntimeError("Refusing unknown test site")
CONFIG = BENCH / "sites" / SITE / "site_config.json"


def run(*args, secret_values=()):
    # Do not print command arguments: site creation includes secret arguments.
    result = subprocess.run(args, cwd=BENCH, capture_output=bool(secret_values), text=True)
    if secret_values:
        output = (result.stdout or "") + (result.stderr or "")
        for secret in secret_values:
            output = output.replace(secret, "[REDACTED]")
        print(output, end="")
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed with exit code {result.returncode}; see output above")


def assert_test_site():
    config = json.loads(CONFIG.read_text())
    if config.get("meixin_test_site") != 1 or not config.get("allow_tests"):
        raise RuntimeError("Refusing operation: isolated test-site flags are missing")
    if config.get("db_host") != "db":
        raise RuntimeError("Refusing operation: unexpected database host")


def bootstrap():
    common_path = BENCH / "sites" / "common_site_config.json"
    common = json.loads(common_path.read_text()) if common_path.exists() else {}
    common.update(
        redis_cache="redis://redis:6379/0",
        redis_queue="redis://redis:6379/1",
        redis_socketio="redis://redis:6379/1",
        default_site=SITE,
        serve_default_site=True,
        developer_mode=0,
        socketio_port=18082,
        webserver_port=18081,
    )
    common_path.write_text(json.dumps(common, indent=2))
    if not CONFIG.exists():
        root_password = Path("/run/secrets/db_root_password").read_text().strip()
        admin_password = Path("/run/secrets/admin_password").read_text().strip()
        run(
            "bench", "new-site", SITE,
            "--db-host", "db", "--db-name", SITE.split(".")[0],
            "--db-root-username", "root", "--db-root-password", root_password,
            "--mariadb-user-host-login-scope", "%",
            "--admin-password", admin_password, "--set-default",
            secret_values=(root_password, admin_password),
        )
        config = json.loads(CONFIG.read_text())
        config.update(meixin_test_site=1, allow_tests=1, developer_mode=0)
        CONFIG.write_text(json.dumps(config, indent=2))
    assert_test_site()
    run("bench", "use", SITE)
    print("Independent test site ready: " + SITE)


def install_python_package():
    # The source is bind-mounted and has no third-party dependencies. A path file
    # keeps container recreation deterministic and avoids downloading build tools.
    purelib = Path(sysconfig.get_paths()["purelib"])
    (purelib / "meixin_admin.pth").write_text(str(BENCH / "apps/meixin_admin") + "\n")


def install():
    assert_test_site()
    install_python_package()
    apps_path = BENCH / "sites" / "apps.txt"
    apps = apps_path.read_text().splitlines() if apps_path.exists() else ["frappe", "erpnext"]
    if "meixin_admin" not in apps:
        apps.append("meixin_admin")
        apps_path.write_text("\n".join(apps) + "\n")
    run("bench", "--site", SITE, "install-app", "meixin_admin")
    run("bench", "--site", SITE, "migrate")


def configure_browser():
    assert_test_site()
    import frappe

    os.chdir(BENCH / "sites")
    frappe.init(site=SITE, sites_path=str(BENCH / "sites"))
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        frappe.db.set_single_value("System Settings", {
            "time_zone": "Asia/Chongqing", "language": "zh", "setup_complete": 1,
        })
        frappe.db.set_value("User", "Administrator", {
            "language": "zh", "time_zone": "Asia/Chongqing",
        })
        frappe.db.commit()
        frappe.clear_cache()
    finally:
        frappe.destroy()
    print("Isolated browser setup: Chinese, Asia/Chongqing, setup complete")


def serve():
    while not CONFIG.exists() or not json.loads(CONFIG.read_text()).get("meixin_test_site"):
        time.sleep(2)
    apps_path = BENCH / "sites" / "apps.txt"
    if "meixin_admin" in apps_path.read_text().splitlines():
        install_python_package()
    # Development-only companion; both processes stop with this isolated container.
    node_paths = sorted(Path("/home/frappe/.nvm/versions/node").glob("*/bin/node"))
    if len(node_paths) != 1:
        raise RuntimeError("Expected one Node runtime in the pinned Frappe image")
    subprocess.Popen([str(node_paths[0]), "apps/frappe/socketio.js"], cwd=BENCH)
    os.execvp("bench", ["bench", "--site", SITE, "serve", "--port", "18081", "--noreload"])


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "bootstrap":
        bootstrap()
    elif action == "install":
        install()
    elif action == "serve":
        serve()
    elif action == "configure-browser":
        configure_browser()
    elif action == "test":
        assert_test_site()
        run("bench", "--site", SITE, "execute", "meixin_admin.tests.run.run")
    else:
        raise SystemExit("Unknown action")
