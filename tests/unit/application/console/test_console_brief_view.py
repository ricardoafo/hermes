"""Tests for the Briefs view of the console (#720).

Invariant: the console shows the supervisor desk's published brief unchanged.
Every item keeps its heading, text and citations in the desk's order, and a
brief that only passed as the digest is marked as one.
"""

from datetime import UTC, datetime

from hermes.application.console.views import BriefSection, ConsoleBrief
from hermes.application.supervisor.briefing import Brief, BriefItem, BriefKind
from hermes.application.supervisor.gate import GateVerdict

_AT = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
_ITEMS = (
    BriefItem("Today", "Health ok.", ("health",)),
    BriefItem("What the engine did", "BTC/USDT: no signal.", ("engine",)),
)


def test_each_item_is_shown_unchanged_in_the_desks_order() -> None:
    """Headings, texts and citations reach the view exactly as the desk wrote them."""
    brief = Brief(BriefKind.DAILY, _ITEMS, {})

    view = ConsoleBrief.published(brief, GateVerdict(passed=True, reasons=()), as_of=_AT)

    assert view == ConsoleBrief(
        kind="daily",
        as_of=_AT,
        sections=(
            BriefSection("Today", "Health ok.", ("health",)),
            BriefSection("What the engine did", "BTC/USDT: no signal.", ("engine",)),
        ),
        digest=False,
    )


def test_a_failed_gate_marks_the_brief_as_the_digest() -> None:
    """When the full brief failed the gate, the shown brief says it is the digest."""
    brief = Brief(BriefKind.DAILY, _ITEMS, {})

    view = ConsoleBrief.published(brief, GateVerdict(passed=False, reasons=("x",)), as_of=_AT)

    assert view.digest is True
