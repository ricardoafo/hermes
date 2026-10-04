"""Tests for the Renta Web campaign layouts config (change 568-tax-pack D-6)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hermes.domain.filing.renta_campaign import CampaignLayout
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.renta import load_renta_campaigns

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_DIR = _PROJECT_ROOT / "config"


def test_repo_config_holds_the_2025_virtual_currency_box() -> None:
    """Invariant: the shipped config gives box 1804 for the 2025 campaign, with its source."""
    (layout,) = load_renta_campaigns(_CONFIG_DIR, env={}).layouts()
    assert (layout.tax_year, layout.transmissions_box, layout.verified_on) == (
        2025,
        "1804",
        date(2026, 10, 4),
    )


def test_layouts_come_back_by_tax_year(tmp_path: Path) -> None:
    """Invariant: layouts are returned oldest campaign first, whatever the file order."""
    (tmp_path / "renta_campaigns.yaml").write_text(
        "campaigns:\n"
        '  2026: {transmissions_box: "1810", source: s, verified_on: "2027-04-01"}\n'
        '  2025: {transmissions_box: "1804", source: s, verified_on: "2026-10-04"}\n',
        encoding="utf-8",
    )
    assert [layout.tax_year for layout in load_renta_campaigns(tmp_path, env={}).layouts()] == [
        2025,
        2026,
    ]


def test_a_box_must_be_four_digits(tmp_path: Path) -> None:
    """Invariant: a mistyped box number is a config error, never printed on the sheet."""
    (tmp_path / "renta_campaigns.yaml").write_text(
        'campaigns:\n  2025: {transmissions_box: "18O4", source: s, verified_on: "2026-10-04"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_renta_campaigns(tmp_path, env={})


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    """Invariant: no campaigns file fails loudly instead of printing no box."""
    with pytest.raises(ConfigError):
        load_renta_campaigns(tmp_path, env={})


def test_layout_type_is_the_domain_layout() -> None:
    """Invariant: the loader hands the domain its own type."""
    layouts = load_renta_campaigns(_CONFIG_DIR, env={}).layouts()
    assert all(isinstance(layout, CampaignLayout) for layout in layouts)
