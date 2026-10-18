"""The supervisor desk's output gate (ADR-012 D-08).

Every text the desk posts passes this gate first. The gate, not a prompt, is
the security boundary: it rejects text that is too long, carries a link, a
code fence or a secret-shaped string, names a strategy outcome (D-06), cites a
reference that is not in the input, or states a number that the input does
not contain. A model cannot invent a figure or a source that survives it.

On failure the caller posts the deterministic digest instead (D-08).

Reference: openspec/archive/365-supervisor-desk-briefings/change.md; issues #365, #496.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

__all__ = ["MAX_CHARS", "GateVerdict", "check", "numbers_in"]

MAX_CHARS: Final = 4000
"""Longest text the gate lets through; a weekly brief fits well inside it."""

_LINK: Final = re.compile(r"https?://|www\.|\]\(|<a\s", re.IGNORECASE)
_FENCE: Final = re.compile(r"```|~~~")
_SECRET: Final = re.compile(
    r"AKIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9_-]{16,}"
    r"|xox[abprs]-[A-Za-z0-9-]+"
    r"|(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|\b\d{8,10}:[A-Za-z0-9_-]{30,}"
    r"|[A-Za-z0-9+/_-]{40,}"
)
# Before certification no AI-facing text may name a strategy outcome (ADR-012 D-06).
_OUTCOME: Final = re.compile(
    r"\b(?:p&l|pnl|profits?|profitable|loss(?:es)?|losing|equity|sharpe|drawdowns?|"
    r"win(?:ning)?\s+rate|winners?|losers?|unrealized|realized|balance|roi|returns?)\b",
    re.IGNORECASE,
)
_NUMBER: Final = re.compile(r"\d+(?:\.\d+)?")
_REFERENCE: Final = re.compile(r"\[([^\[\]\s]+)\]")


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Whether a text may be posted, and every reason it may not.

    Attributes:
        passed: ``True`` when no rule was broken.
        reasons: One plain line per broken rule; empty when ``passed``.
    """

    passed: bool
    reasons: tuple[str, ...]


def numbers_in(value: object) -> frozenset[str]:
    """Return every number written in ``value``'s canonical JSON.

    Args:
        value: JSON-ready data: the input a text was written from.
    """
    return frozenset(_NUMBER.findall(json.dumps(value, sort_keys=True, default=str)))


def check(
    text: str,
    *,
    inputs: object,
    references: Collection[str],
    max_chars: int = MAX_CHARS,
) -> GateVerdict:
    """Check ``text`` against the gate rules.

    Args:
        text: What would be posted.
        inputs: The JSON-ready data the text was written from.
        references: The references a text may cite as ``[ref]``.
        max_chars: Length cap.

    Returns:
        The verdict, listing every broken rule.
    """
    reasons: list[str] = []
    if not text.strip():
        reasons.append("empty text")
    if len(text) > max_chars:
        reasons.append(f"longer than {max_chars} characters")
    if _LINK.search(text):
        reasons.append("contains a link")
    if _FENCE.search(text):
        reasons.append("contains a code fence")
    if _SECRET.search(text):
        reasons.append("contains a secret-shaped string")
    words = sorted({m.lower() for m in _OUTCOME.findall(text)})
    if words:
        reasons.append(f"names a strategy outcome: {', '.join(words)}")
    unknown_refs = sorted(set(_REFERENCE.findall(text)) - set(references))
    if unknown_refs:
        reasons.append(f"cites unknown reference(s): {', '.join(unknown_refs)}")
    # A reference is checked against ``references`` above; its digits are not figures.
    prose = _REFERENCE.sub(" ", text)
    uncited = sorted(set(_NUMBER.findall(prose)) - numbers_in(inputs))
    if uncited:
        reasons.append(f"states number(s) not in the input: {', '.join(uncited)}")
    return GateVerdict(passed=not reasons, reasons=tuple(reasons))
