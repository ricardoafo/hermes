"""Unit tests for scripts/dev/footprint-check.py (#1198).

Invariants under test:

- The repo as committed has no footprint outside the carve-out.
- A listed name fails as a whole word in any case, and not inside a longer
  word.
- The carve-out (``docs/research/``, ``docs/architecture/``) and the release
  notes are not scanned.
- The bot's account name fails in Markdown prose and passes in inline code or
  a fenced block.
- A value on the scrub list fails wherever it appears, including an email with
  a ``+tag``; a public or tailnet IP fails unless it is a known fake; a
  documentation or private address passes; an AI attribution footer fails.
- The signed research record (``docs/experiments/``) is left out of the scrub.
"""

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.paths import REPO_ROOT as _ROOT


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "footprint_check", _ROOT / "scripts" / "dev" / "footprint-check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["footprint_check"] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load()
_NAMES = ["acmetrader"]


def test_repo_has_no_footprint() -> None:
    """No tracked file outside the carve-out names a listed project."""
    names = CHECK.load_names((_ROOT / CHECK.NAMES_FILE).read_text(encoding="utf-8"))
    assert names
    assert CHECK.footprint_problems(_ROOT, CHECK.tracked_files(_ROOT), names) == []


def test_load_names_skips_comments_and_blanks() -> None:
    """Comment lines and blank lines are not names."""
    assert CHECK.load_names("# header\n\nacmetrader\n  beta  # note\n") == ["acmetrader", "beta"]


def test_name_matches_whole_words_in_any_case() -> None:
    """``AcmeTrader`` matches; ``acmetraders`` and ``acmetrader-x`` do not."""
    pattern = CHECK.name_pattern(_NAMES)
    text = "uses AcmeTrader here\nacmetraders\nacmetrader-x\n(acmetrader)"
    assert CHECK.name_hits(text, pattern) == [(1, "AcmeTrader"), (4, "acmetrader")]


def test_problem_names_file_and_line(tmp_path: Path) -> None:
    """A hit is reported with its path, line and the name found."""
    guide = tmp_path / "guide.md"
    guide.write_text("ok\nAs AcmeTrader does it.\n", encoding="utf-8")
    assert CHECK.footprint_problems(tmp_path, [guide], _NAMES) == [
        "guide.md:2: names an outside project (AcmeTrader); describe the pattern instead, "
        "or move the citation into docs/research/"
    ]


def test_carve_out_and_release_notes_are_not_scanned() -> None:
    """Research, ADRs and release notes are left out of the tracked-file scan."""
    scanned = {path.relative_to(_ROOT).as_posix() for path in CHECK.tracked_files(_ROOT)}
    assert "docs/research/README.md" not in scanned
    assert "docs/architecture/adr-011-paper-trading-engine.md" not in scanned
    assert "CHANGELOG.md" not in scanned
    assert "docs/guides/dev-workflow.md" in scanned


def test_bot_account_fails_in_prose_only() -> None:
    """The account name passes in inline code or a fence and fails in prose."""
    account = CHECK.BOT_ACCOUNT
    markdown = (
        f"Run `gh auth switch --user {account}`.\n"
        "```bash\n"
        f"git config user.name {account}\n"
        "```\n"
        f"Then {account} opens the PR, as `gh auth\nswitch --user {account}` shows.\n"
    )
    assert CHECK.bot_prose_lines(markdown) == [5]


def test_repo_has_no_private_value() -> None:
    """No tracked file outside the research record holds a private value."""
    assert CHECK.scrub_problems(_ROOT, CHECK.tracked_files(_ROOT, CHECK.SCRUB_NOT_SCANNED)) == []


def test_scrub_skips_the_signed_research_record() -> None:
    """``docs/experiments/`` and the lock file are left out of the scrub; the ADRs are scanned."""
    scanned = {
        path.relative_to(_ROOT).as_posix()
        for path in CHECK.tracked_files(_ROOT, CHECK.SCRUB_NOT_SCANNED)
    }
    assert not any(name.startswith("docs/experiments/") for name in scanned)
    assert "uv.lock" not in scanned
    assert "docs/architecture/adr-004-exchange-decision.md" in scanned


def test_scrub_list_value_fails_in_any_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """A listed account or email fails, the email also with a ``+tag`` or in capitals."""
    listed = {
        hashlib.sha256(value.encode()).hexdigest() for value in ("u123456", "someone@example.org")
    }
    monkeypatch.setattr(CHECK, "SCRUBBED_DIGESTS", frozenset(listed))
    text = "user u123456 here\nmail Someone+bot@Example.org\nuser u1234567 is longer\nok\n"
    assert CHECK.scrub_hits(text) == [
        (1, "a value on the scrub list"),
        (2, "a value on the scrub list"),
    ]


def _ip(*octets: int) -> str:
    """Return a dotted address built at run time, so this file holds none itself."""
    return ".".join(str(octet) for octet in octets)


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        (f"ssh root@{_ip(8, 8, 8, 8)}", True),
        (f"tailnet {_ip(100, 100, 1, 2)}", True),
        ("docs use 203.0.113.10", False),
        ("lan 192.168.1.20 and 10.0.0.5", False),
        ("loopback 127.0.0.1", False),
        ("fake 100.64.0.1 in a test", False),
        ("version 1.2.3.4.5 is not an address", False),
    ],
)
def test_public_and_tailnet_addresses_fail(line: str, flagged: bool) -> None:
    """Only a public or tailnet address that is not a known fake is reported."""
    hits = CHECK.scrub_hits(line)
    assert hits == (
        [(1, "a public or tailnet IP address; use 203.0.113.0/24 in examples")] if flagged else []
    )


def test_ai_footer_fails_only_as_a_trailer() -> None:
    """A footer line fails; prose quoting the rule does not."""
    text = "Co-Authored-By: Claude <x@y>\nNever add `Co-Authored-By: Claude` lines.\n"
    assert CHECK.scrub_hits(text) == [(1, "an AI attribution footer")]


def test_scrub_problem_names_file_and_line(tmp_path: Path) -> None:
    """A private value is reported with its path and line."""
    guide = tmp_path / "guide.md"
    guide.write_text(f"ok\nexport HOST={_ip(8, 8, 4, 4)}\n", encoding="utf-8")
    assert CHECK.scrub_problems(tmp_path, [guide]) == [
        "guide.md:2: holds a public or tailnet IP address; use 203.0.113.0/24 in examples"
    ]
