"""Operator ruling 2026-10-03: every value is EUR, USDT is a secondary detail line.

Each test protects one rule: coin prices convert through the rate the read
model provides, USDT stays out of the home and list views, the detail labels
its two size figures, and the stop column is plain price plus distance.
"""

# The console prints the typographic minus (U+2212) and times sign on purpose.
# ruff: noqa: RUF001

from __future__ import annotations

from decimal import Decimal

import pytest

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import Surface
from hermes.application.console.format import fmt_stop
from tests.unit.application.console._screens import render


def test_position_prices_convert_at_the_read_model_rate() -> None:
    """ETH entry 2,618 USDT at 0.8567 EUR per USDT is 2,242.8 EUR."""
    row = FixtureReadModel.load("ok-two-positions").positions()[0]
    assert row.entry_eur == Decimal("2618") * Decimal("0.8567")


@pytest.mark.parametrize("screen", ["positions", "portfolio", "activity", "overview"])
def test_no_usdt_on_home_and_list_views(screen: str) -> None:
    """USDT appears only in the position detail and in JSON."""
    text = render("ok-two-positions", screen, surface=Surface.OPERATOR_CLI)
    assert "USDT" not in text


def test_detail_has_one_secondary_usdt_line() -> None:
    """The detail quotes the original USDT prices on one dim line."""
    text = render("ok-two-positions", "position-detail", surface=Surface.OPERATOR_CLI)
    quoted = [line for line in text.splitlines() if "USDT" in line]
    assert quoted == ["quoted in USDT · entry 2,618 · now 2,672 · stop 2,580 · high 2,690"]


def test_detail_labels_both_size_figures() -> None:
    """Size, worth now and cost are named: cost is size x entry plus the buy fee."""
    text = render("ok-two-positions", "position-detail", surface=Surface.OPERATOR_CLI)
    assert "size 0.0350 ETH · now worth €80.12 · cost €78.58" in text


def test_started_line_is_in_eur() -> None:
    """The starting capital reads in EUR with its start day."""
    text = render("ok-two-positions", "portfolio", surface=Surface.OPERATOR_CLI)
    assert "started €1,000.00 on 27 Sep" in text


def test_stop_cell_is_price_and_one_decimal_distance() -> None:
    """The stop reads as a EUR price and the distance below the price."""
    assert fmt_stop(Decimal("2166.4"), Decimal("3.94")) == "€2,166 · −3.9%"


def test_detail_names_atr_as_average_range() -> None:
    """The detail explains ATR in words; the list never names it."""
    text = render("ok-two-positions", "position-detail", surface=Surface.OPERATOR_CLI)
    assert "3.1 × average range (ATR)" in text
