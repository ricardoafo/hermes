"""Pytest plugin: fail session if SEV-1 tests were skipped without opt-in.

A test is SEV-1-annotated if the source has `# SEV-1:` comment above `def test_*`.
If any such test is skipped and `PYTEST_SEV1_ALLOW_SKIP` env var is not set,
the session exits 1 (post-run).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_SEV1_RE = re.compile(r"#\s*SEV-1:", re.IGNORECASE)
_skipped_sev1: list[str] = []


def _is_sev1_test(nodeid: str) -> bool:
    file_part = nodeid.split("::", maxsplit=1)[0]
    try:
        src = Path(file_part).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    test_name = nodeid.rsplit("::", maxsplit=1)[-1].split("[", maxsplit=1)[0]
    for i, line in enumerate(src):
        if f"def {test_name}" in line:
            for j in range(max(0, i - 3), i):
                if _SEV1_RE.search(src[j]):
                    return True
    return False


def pytest_report_teststatus(report: pytest.TestReport) -> None:
    if report.when == "setup" and report.skipped and _is_sev1_test(report.nodeid):
        _skipped_sev1.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _skipped_sev1 and not os.environ.get("PYTEST_SEV1_ALLOW_SKIP"):
        for nid in _skipped_sev1:
            print(f"SEV1_GUARD: SEV-1 test skipped without opt-in: {nid}")
        session.exitstatus = 1
