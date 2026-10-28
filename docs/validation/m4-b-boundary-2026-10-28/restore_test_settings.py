"""Restore only this run's virtual test settings from its pre-test backup; no database restore."""
import gzip
import json
import re
from pathlib import Path

import frappe


def run():
    from drill import guard, snapshot, digest

    guard()
    evidence = Path('/home/frappe/frappe-bench/sites/m4b-upgrade-evidence/boundary-20261028')
    original = json.loads((evidence / 'payroll-regression.json').read_text())['before']
    backup = Path('/home/frappe/frappe-bench/sites/test_meixin_m4.localhost/private/backups/20261028_063827-test_meixin_m4_localhost-database.sql.gz')
    data = gzip.open(backup, 'rt').read()
    pairs = re.findall(r"\('MX Settings','([^']+)',(NULL|'[^'\\]*')\)", data)
    rows = [dict(field=field, value=None if value == 'NULL' else value[1:-1]) for field, value in pairs]
    rows.sort(key=lambda row: row['field'])
    assert len(rows) == original['facts']['MX Settings']['count']
    assert digest(rows) == original['facts']['MX Settings']['sha256']
    frappe.db.sql("DELETE FROM tabSingles WHERE doctype='MX Settings'")
    for row in rows:
        frappe.db.sql("INSERT INTO tabSingles (doctype,field,value) VALUES ('MX Settings',%s,%s)",
                      (row['field'], row['value']))
    restored = snapshot()
    assert restored == original, 'All virtual history and settings must match pre-run snapshot'
    frappe.db.commit()
    frappe.clear_document_cache('MX Settings', 'MX Settings')
    result = dict(date='2026-10-28', site=frappe.local.site, restored_only_test_settings=True,
                  original_snapshot_matches=True, database_restore=False, production_accessed=False)
    (evidence / 'test-settings-restoration.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
