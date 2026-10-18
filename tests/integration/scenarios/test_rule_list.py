"""No pre-registration rule may be left without a test or a reason (issue #689).

The bullets are read from the signed document, so a rule cannot be missed by
forgetting to list it, and a new version of the document fails here until the
list in ``rules.py`` is reviewed against it.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from tests.integration.scenarios import rules
from tests.integration.scenarios.rules import IN_CODE, PRE_REGISTERED, PRE_REGISTRATION, Rule
from tests.support.node_ids import PROJECT_ROOT, names_a_test

pytestmark = pytest.mark.integration

_SECTIONS = {
    "5.1 Entry (long-only for MVP1)": "5.1",
    "5.2 Exit": "5.2",
    "5.3 Order type": "5.3",
    "5.4 Position sizing": "5.4",
    "5.5 Concurrency + correlation": "5.5",
    "6.1 Fees (point-in-time)": "6.1",
    "6.2 Slippage model (backtest)": "6.2",
    "6.3 Fill assumptions": "6.3",
    "Backtest phase (do not proceed to shadow)": "10 backtest",
    "Shadow phase (do not proceed to live)": "10 shadow",
    "Live phase (HALT — declare strategy dead)": "10 live",
    "Post-kill protocol": "10 post-kill",
}
_ISSUE = re.compile(r"^issue #\d+: \S")
_NOTEBOOK = "docs/experiments/swing-mvp1/lab-notebook.md"


def _document() -> str:
    return (PROJECT_ROOT / PRE_REGISTRATION).read_text(encoding="utf-8")


def _bullets() -> dict[str, list[str]]:
    """Return the bullets of every rule section, keyed like ``PRE_REGISTERED``."""
    found: dict[str, list[str]] = {}
    section = None
    for line in _document().splitlines():
        heading = re.match(r"^#{2,3} (.+)$", line)
        if heading:
            section = _SECTIONS.get(heading.group(1))
            continue
        if section is not None and line.startswith("- "):
            found.setdefault(section, []).append(line[2:])
    return found


def _all_rules() -> list[Rule]:
    return [rule for listed in PRE_REGISTERED.values() for rule in listed] + list(IN_CODE)


def test_the_signed_pre_registration_is_the_one_the_list_was_reviewed_against() -> None:
    """A changed document fails until ``rules.py`` is reviewed and its hash updated."""
    digest = hashlib.sha256((PROJECT_ROOT / PRE_REGISTRATION).read_bytes()).hexdigest()
    assert digest == rules.PRE_REGISTRATION_SHA256


def test_every_rule_in_the_document_has_an_entry_in_order() -> None:
    """Each bullet of §5.1 to §6.3 and §10 matches one listed rule, in the same order."""
    listed = {section: [r.starts for r in found] for section, found in PRE_REGISTERED.items()}
    bullets = _bullets()
    unmatched = {
        section: [
            (bullet[:40], start)
            for bullet, start in zip(bullets.get(section, []), starts, strict=False)
            if not bullet.startswith(start)
        ]
        for section, starts in listed.items()
    }
    counts = {section: len(found) for section, found in bullets.items()}
    assert (counts, {s: u for s, u in unmatched.items() if u}) == (
        {section: len(found) for section, found in PRE_REGISTERED.items()},
        {},
    )


def test_every_rule_has_exactly_one_kind_of_answer() -> None:
    """A rule is proved by tests, or a gap with an issue, or outside the engine: never none."""
    wrong = [
        rule.starts
        for rule in _all_rules()
        if [bool(rule.proved_by), bool(rule.gap), bool(rule.outside)].count(True) != 1
        or (rule.gap and not _ISSUE.match(rule.gap))
    ]
    assert wrong == []


def test_every_named_proof_is_a_test_that_exists() -> None:
    """A renamed or deleted test cannot leave a rule pointing at nothing."""
    missing = [node for rule in _all_rules() for node in rule.proved_by if not names_a_test(node)]
    assert missing == []


def test_every_deviation_names_a_notebook_entry_and_proves_its_replacement() -> None:
    """A rule replaced by a §11 deviation names an existing lab-notebook entry and has tests.

    A deviation that is not logged in the notebook, or that only names one,
    would leave the replaced rule with no proof of what serve does instead.
    """
    headings = {
        line.removeprefix("### ")
        for line in (PROJECT_ROOT / _NOTEBOOK).read_text(encoding="utf-8").splitlines()
        if line.startswith("### ")
    }
    wrong = [
        rule.starts
        for rule in _all_rules()
        if rule.deviation and (rule.deviation not in headings or not rule.proved_by)
    ]
    assert wrong == []


def test_the_exit_rule_names_the_deviation_that_replaced_it() -> None:
    """§5.3's stop-market exit is the rule the 2026-10-18 deviation replaced (issue #1157)."""
    exit_rule = next(r for r in PRE_REGISTERED["5.3"] if r.starts.startswith("Exit:"))

    assert exit_rule.deviation.startswith(
        "2026-10-18T15:46:01Z | shadow | operator | §11 deviation"
    )
