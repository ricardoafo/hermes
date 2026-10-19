"""Unit tests for scripts/dev/agents-md-check.py (#1195).

Invariants under test:

- The committed AGENTS.md is within its line budget and names no ADR range.
- One line over the budget fails; exactly at the budget passes.
- Any spelling of a fixed ADR range fails; a single ADR reference passes.
"""

import importlib.util
import sys
from types import ModuleType

import pytest

from tests.support.paths import REPO_ROOT as _ROOT


def _load() -> ModuleType:
    """Import the script module (not a package)."""
    spec = importlib.util.spec_from_file_location(
        "agents_md_check", _ROOT / "scripts" / "dev" / "agents-md-check.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["agents_md_check"] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load()


def test_committed_agents_md_passes() -> None:
    """AGENTS.md as committed fits the budget and names no ADR range."""
    assert CHECK.agents_md_problems((_ROOT / "AGENTS.md").read_text(encoding="utf-8")) == []


def test_budget_boundary() -> None:
    """Three lines pass a budget of 3; four lines fail it with the count named."""
    assert CHECK.agents_md_problems("a\nb\nc\n", budget=3) == []
    assert CHECK.agents_md_problems("a\nb\nc\nd\n", budget=3) == [
        "AGENTS.md has 4 lines, over the 3-line budget; move the reasons to "
        "docs/decisions/2026-10-18-agents-md-rationale.md"
    ]


@pytest.mark.parametrize(
    "line", ["ADRs 001-010 decide", "see ADR-001 to ADR-010", "ADRs 1\u201329", "ADR 003 - 009"]
)
def test_adr_range_fails(line: str) -> None:
    """A fixed ADR range is reported with its line number."""
    problems = CHECK.agents_md_problems(f"intro\n{line}\n")
    assert len(problems) == 1
    assert problems[0].startswith("AGENTS.md:2: names the ADR range")


@pytest.mark.parametrize("line", ["per ADR-009 §6", "ADR-003 §04 and ADR-011 D-10"])
def test_single_adr_reference_passes(line: str) -> None:
    """Citing one ADR, or two separately, is not a range."""
    assert CHECK.agents_md_problems(line) == []
