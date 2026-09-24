"""Small source checks that do not need a Frappe site or third-party packages."""

import json
import re
import subprocess
import sys
from pathlib import Path


SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
}
SENSITIVE_NAMES = {"site_config.json", "common_site_config.json", ".env"}


def main():
    paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    failures = []
    counts = {"Python": 0, "JSON": 0}
    for raw_path in filter(None, paths):
        path = Path(raw_path.decode(sys.getfilesystemencoding()))
        if path.name in SENSITIVE_NAMES or path.suffix in {".pem", ".key"}:
            failures.append((path, 1, "sensitive file tracked"))
            continue
        data = path.read_bytes()
        for label, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(data):
                failures.append((path, data.count(b"\n", 0, match.start()) + 1, label))
        if path.suffix == ".py":
            counts["Python"] += 1
            try:
                compile(data, str(path), "exec")
            except SyntaxError as exc:
                failures.append((path, exc.lineno or 1, "Python syntax"))
        elif path.suffix == ".json":
            counts["JSON"] += 1
            try:
                json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                failures.append((path, getattr(exc, "lineno", 1), "JSON syntax"))
    for path, line, label in failures:
        print(f"::error file={path.as_posix()},line={line}::{label}")
    print(f"Checked {counts['Python']} Python and {counts['JSON']} JSON files; {len(failures)} errors")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
