"""Tests for the shape table and the shape renderer.

Invariants protected here:

- every (family, channel) pair has a shape, so no event can fall through;
- the table encodes the recommendation (trade lifecycle compact on Telegram
  and verbose on Slack, health compact on both, reports one line on Telegram
  and compact on Slack);
- changing one entry of the table changes the rendered message of that family
  only, which is what makes a later swap a one-line edit;
- every shape ends with exactly one correlation ref.

Reference: docs/decisions/2026-10-04-notification-templates-v2.md.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.messages import shapes as shapes_module
from hermes.application.messages import slack, telegram
from hermes.application.messages.shapes import (
    SHAPES,
    Channel,
    Family,
    Note,
    Shape,
    render_lines,
    render_parent_and_plan,
    shape_for,
)
from tests.support.notification_samples import samples

_NOTE = Note(
    minimal="▲ Long BTC",
    compact=("▲ Entry long BTC", "stop 1"),
    verbose=("▲ Entry long BTC (UTC)",),
    footer=("strat",),
    plan=("Plan: stop 1", "Actual: withheld"),
)


def test_every_family_and_channel_has_a_shape() -> None:
    """No (family, channel) pair is missing from SHAPES."""
    pairs = {(family, channel) for family in Family for channel in Channel}

    assert set(SHAPES) == pairs


@pytest.mark.parametrize(
    ("family", "channel", "shape"),
    [
        (Family.TRADE_LIFECYCLE, Channel.TELEGRAM, Shape.COMPACT),
        (Family.TRADE_LIFECYCLE, Channel.SLACK, Shape.VERBOSE),
        (Family.HEALTH_OPS, Channel.TELEGRAM, Shape.COMPACT),
        (Family.HEALTH_OPS, Channel.SLACK, Shape.COMPACT),
        (Family.REPORTS, Channel.TELEGRAM, Shape.MINIMAL),
        (Family.REPORTS, Channel.SLACK, Shape.COMPACT),
    ],
)
def test_table_encodes_the_recommendation(family: Family, channel: Channel, shape: Shape) -> None:
    """The shipped shapes are the ones the proposal recommends."""
    assert shape_for(family, channel) is shape


def test_shape_table_is_read_only() -> None:
    """SHAPES is a mapping proxy, so no caller can change a shape at runtime."""
    with pytest.raises(TypeError):
        SHAPES["x"] = next(iter(SHAPES.values()))  # type: ignore[index]


def test_minimal_is_one_line_ending_with_the_ref() -> None:
    """Minimal renders a single line: the text, then the ref."""
    assert render_lines(_NOTE, Shape.MINIMAL, "…K7Q2ZP") == ("▲ Long BTC · …K7Q2ZP",)


def test_compact_puts_footer_parts_and_ref_on_the_last_line() -> None:
    """Compact keeps the body and ends with the footer parts and the ref."""
    assert render_lines(_NOTE, Shape.COMPACT, "…K7Q2ZP") == (
        "▲ Entry long BTC",
        "stop 1",
        "strat · …K7Q2ZP",
    )


def test_verbose_uses_the_verbose_body() -> None:
    """Verbose renders the verbose body, then the same footer line."""
    assert render_lines(_NOTE, Shape.VERBOSE, "…K7Q2ZP") == (
        "▲ Entry long BTC (UTC)",
        "Plan: stop 1",
        "Actual: withheld",
        "strat · …K7Q2ZP",
    )


def test_parent_and_plan_split_a_verbose_note_with_a_plan() -> None:
    """A verbose note with a plan gives the headline and ref as parent and the plan apart."""
    assert render_parent_and_plan(_NOTE, Shape.VERBOSE, "…K7Q2ZP") == (
        ("▲ Entry long BTC (UTC)", "strat · …K7Q2ZP"),
        ("Plan: stop 1", "Actual: withheld"),
    )


def test_parent_and_plan_keep_other_shapes_whole() -> None:
    """A compact note, or a verbose one with no plan, is one parent with an empty plan."""
    bare = Note(minimal="m", compact=("c",), verbose=("v",))

    assert (
        render_parent_and_plan(_NOTE, Shape.COMPACT, "…ABC123"),
        render_parent_and_plan(bare, Shape.VERBOSE, "…ABC123"),
    ) == (
        (("▲ Entry long BTC", "stop 1", "strat · …ABC123"), ()),
        (("v", "…ABC123"), ()),
    )


def test_note_without_footer_ends_with_the_bare_ref() -> None:
    """A note with no footer parts ends with the ref alone."""
    note = Note(minimal="m", compact=("c",), verbose=("v",))

    assert render_lines(note, Shape.COMPACT, "…ABC123")[-1] == "…ABC123"


@given(
    shape=st.sampled_from(list(Shape)),
    ref=st.text(alphabet="ABCDEF0123456789", min_size=6, max_size=6),
)
def test_every_shape_contains_the_ref_once_at_the_end(shape: Shape, ref: str) -> None:
    """Whatever the shape, the ref appears exactly once, on the last line."""
    lines = render_lines(_NOTE, shape, ref)

    assert (lines[-1].endswith(ref), sum(line.count(ref) for line in lines)) == (True, 1)


def test_swapping_one_table_entry_changes_only_that_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving reports on Slack to minimal changes the digest and leaves a signal untouched."""
    events = samples()
    digest_before = slack.slack_template(events["daily-digest"]).text
    signal_before = slack.slack_template(events["signal-entry"]).text
    swapped = dict(SHAPES)
    swapped[(Family.REPORTS, Channel.SLACK)] = Shape.MINIMAL
    monkeypatch.setattr(shapes_module, "SHAPES", MappingProxyType(swapped))

    digest_after = slack.slack_template(events["daily-digest"]).text
    signal_after = slack.slack_template(events["signal-entry"]).text

    assert (digest_after != digest_before, signal_after == signal_before) == (True, True)


def test_swapping_trade_lifecycle_on_telegram_to_verbose_adds_the_plan_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-line table edit gives Telegram the verbose plan block for a signal."""
    swapped = dict(SHAPES)
    swapped[(Family.TRADE_LIFECYCLE, Channel.TELEGRAM)] = Shape.VERBOSE
    monkeypatch.setattr(shapes_module, "SHAPES", MappingProxyType(swapped))

    text = telegram.telegram_template(samples()["signal-entry"])

    assert "Actual: withheld until certification" in text
