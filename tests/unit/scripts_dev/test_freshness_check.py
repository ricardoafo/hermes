"""Unit tests for scripts/dev/freshness-check.py (#906 follow-up).

Invariants under test:

- Every pin pattern finds the real pin in the shipped tree, so a reworded
  Dockerfile or workflow line can't silently drop a tool from the report.
- Versions compare numerically (2.1.112 is newer than 2.1.99).
- Only pins older than the latest release are reported.
- The newest stable tag ignores ``v``/``bun-`` prefixes and pre-releases.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "freshness_check", _ROOT / "scripts" / "dev" / "freshness-check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["freshness_check"] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load()


@pytest.mark.parametrize("source", CHECK.SOURCES, ids=lambda s: f"{s.tool}@{s.path}")
def test_every_source_finds_a_pin_in_the_shipped_tree(source) -> None:
    """Each pattern matches at least one versioned pin where it says the pin lives."""
    pins = CHECK.find_pins(source)
    assert {p.tool for p in pins} == {source.tool}, f"no {source.tool} pin in {source.path}"
    assert [p for p in pins if len(CHECK.version_key(p.current)) < 2] == []


def test_find_pins_reports_each_file_and_version_once(tmp_path: Path) -> None:
    """Two steps pinning the same version in one file give one row."""
    (tmp_path / "w.yml").write_text("run: x semgrep==1.2.3\nrun: y semgrep==1.2.3\n")
    source = CHECK.Source("semgrep", "*.yml", r"semgrep==([0-9][^\s]*)", lambda: "9")
    assert CHECK.find_pins(source, tmp_path) == [CHECK.Pin("semgrep", "1.2.3", "w.yml")]


def test_versions_compare_numerically() -> None:
    """2.1.112 sorts after 2.1.99, which a string compare gets wrong."""
    assert CHECK.version_key("2.1.112") > CHECK.version_key("2.1.99")


def test_only_older_pins_are_reported() -> None:
    """A pin equal to the latest release is not behind; an older one is."""
    old = CHECK.Pin("uv", "0.12.5", "a")
    current = CHECK.Pin("uv", "0.12.23", "b")
    assert CHECK.behind([old, current], {"uv": "0.12.23"}) == [(old, "0.12.23")]


def test_newest_stable_tag_skips_prefixes_and_prereleases() -> None:
    """``bun-v1.4.2`` beats ``v1.3.6``; ``v1.5.0-rc1`` is not stable."""
    tags = ["v1.3.6", "bun-v1.4.2", "v1.5.0-rc1", "canary"]
    assert CHECK.newest_stable(tags) == "1.4.2"


def test_report_lists_each_stale_pin() -> None:
    """The Markdown row names the tool, both versions and the file."""
    report = CHECK.render([(CHECK.Pin("Bun", "1.3.6", "Dockerfile"), "1.4.2")])
    assert "| Bun | 1.3.6 | 1.4.2 | `Dockerfile` |" in report
