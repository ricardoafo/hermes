"""Unit tests for scripts/dev/footprint-check.py (#1198).

Invariants under test:

- The repo as committed has no footprint outside the carve-out.
- A listed name fails as a whole word in any case, and not inside a longer
  word.
- The carve-out (``docs/research/``, ``docs/architecture/``) and the release
  notes are not scanned.
- The bot's account name fails in Markdown prose and passes in inline code or
  a fenced block.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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
