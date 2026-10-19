"""Tests for the supervisor desk's output gate (#365, #496; ADR-012 D-08).

Invariants protected here:

- a text that breaks no rule passes, with no reasons;
- a link, a code fence or a secret-shaped string is rejected;
- a strategy outcome word is rejected (ADR-012 D-06);
- a cited reference must be one the caller allows;
- every number in the text must exist in the input, so a model cannot invent
  a figure; any number taken from the input passes;
- over-long and empty texts are rejected.

The gate is pure; the briefing tests in ``test_briefing.py`` exercise it on
composed briefs.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.supervisor.gate import MAX_CHARS, check, numbers_in

_INPUTS = {"counts": {"signals": 3}, "now": "2026-10-03T16:10:00+00:00"}
_REFS = frozenset({"health", "signals", "decision:DJ-7"})


def _reasons(text: str) -> tuple[str, ...]:
    return check(text, inputs=_INPUTS, references=_REFS).reasons


def test_a_clean_text_passes() -> None:
    """Numbers and references that exist in the input pass with no reasons."""
    verdict = check(
        "3 signals since 2026-10-03 16:10 UTC [signals] [decision:DJ-7]",
        inputs=_INPUTS,
        references=_REFS,
    )

    assert (verdict.passed, verdict.reasons) == (True, ())


@pytest.mark.parametrize(
    "text",
    ["see https://example.org", "see www.example.org", "[here](signals)", '<a href="x">'],
)
def test_links_are_rejected(text: str) -> None:
    """Any link form is rejected: the desk posts no URL a model wrote."""
    assert "contains a link" in _reasons(text)


@pytest.mark.parametrize("text", ["```\nrm -rf\n```", "~~~\nx\n~~~"])
def test_code_fences_are_rejected(text: str) -> None:
    """Code fences are rejected."""
    assert "contains a code fence" in _reasons(text)


@pytest.mark.parametrize(
    "text",
    [
        "key AKIAABCDEFGHIJKLMNOP",  # gitleaks:allow
        "token sk-abcdefghijklmnopqrstu",  # gitleaks:allow
        "slack xoxb-abc-def",  # gitleaks:allow
        "gh ghp_abcdefghijklmnopqrstuvwxyz",  # gitleaks:allow
        "-----BEGIN RSA PRIVATE KEY",  # gitleaks:allow
        "opaque " + "a" * 40,
    ],
)
def test_secret_shaped_strings_are_rejected(text: str) -> None:
    """Credential-shaped strings are rejected wherever they come from."""
    assert "contains a secret-shaped string" in _reasons(text)


@pytest.mark.parametrize(
    "word", ["P&L", "pnl", "profit", "losses", "equity", "Sharpe", "drawdown", "win rate"]
)
def test_outcome_words_are_rejected(word: str) -> None:
    """Strategy outcomes stay out of every AI-facing text until certification."""
    reasons = _reasons(f"The {word} looks fine.")

    assert any(r.startswith("names a strategy outcome") for r in reasons)


def test_an_unknown_reference_is_rejected() -> None:
    """A citation the input does not hold is an invented source."""
    assert "cites unknown reference(s): ledger:42" in _reasons("see [ledger:42]")


def test_a_number_not_in_the_input_is_rejected() -> None:
    """A figure the input does not contain is invented, so it is rejected."""
    assert "states number(s) not in the input: 17" in _reasons("17 signals [signals]")


@given(st.integers(min_value=0, max_value=10**9))
def test_any_number_from_the_input_passes(n: int) -> None:
    """Every number the input contains may be stated."""
    verdict = check(f"{n} signals", inputs={"counts": n}, references=())

    assert verdict.passed


def test_long_and_empty_texts_are_rejected() -> None:
    """The length cap and the empty text are both refused."""
    assert f"longer than {MAX_CHARS} characters" in _reasons("x " * MAX_CHARS)
    assert "empty text" in _reasons("  ")


def test_numbers_in_reads_every_number_in_canonical_json() -> None:
    """Numbers inside strings and values are both found."""
    assert numbers_in({"a": 1.5, "b": "4h at 16:10"}) == {"1.5", "4", "16", "10"}
