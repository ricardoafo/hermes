"""Tests for ``hermes tax`` (change tax-estimates R-5, R-6).

Reference: ADR-016 decisions 1, 10 and 12.
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.tax.netting import CarriedLoss, TaxpayerYear
from hermes.domain.trading.order import OrderSide
from hermes.entrypoints import cli as cli_module
from hermes.entrypoints import tax as tax_module
from hermes.infrastructure.config.tax import TaxpayerProfile, load_tax_scales
from tests.unit.adapters.ledger.test_sqlite_trade_ledger import _record, _trade

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _PROJECT_ROOT / "config"
_NOW = datetime(2026, 1, 2, 18, tzinfo=UTC)
_PROFILE = """\
years:
  2026:
    general_income_eur: "31337"
    other_savings_income_eur: "0"
    losses_carried_forward:
      - { origin_year: 2023, amount_eur: "4.5" }
"""


def _ledger(path: Path, *, sell: bool = True, priced: bool = True) -> Path:
    """Write a ledger with a BUY (125 USDT + 1.25 fee) and optionally a SELL at 150."""
    conn = sqlite3.connect(path)
    try:
        ledger = SqliteLedger(conn)
        _record(ledger, _trade(OrderSide.BUY, "1", "125", "1.25", minutes=0, priced=priced))
        if sell:
            _record(ledger, _trade(OrderSide.SELL, "1", "150", "1.25", minutes=10))
    finally:
        conn.close()
    return path


def _run(tmp_path: Path, *args: str, profile: str | None = _PROFILE) -> tuple[int, str]:
    profile_path = tmp_path / "taxpayer.yaml"
    if profile is not None:
        profile_path.write_text(profile, encoding="utf-8")
    out = io.StringIO()
    code = tax_module.main(
        ["--config-dir", str(_CONFIG_DIR), *args],
        stream=out,
        now=lambda: _NOW,
        env={"HERMES_TAXPAYER_PROFILE": str(profile_path)},
    )
    return code, out.getvalue()


def test_text_output_is_labelled_estimate_and_shadow(tmp_path: Path) -> None:
    """Gain 18 EUR less a 4.50 carried loss, salary above the minimum: tax 2.565 -> 2.57."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db))
    assert code == 0
    assert out.splitlines()[0] == "hermes tax 2026 (estimate · shadow (no real tax due))"
    assert "hermes tax           2.57 EUR" in out
    assert "carried losses used  4.50 EUR" in out


def test_text_band_line_in_the_top_band(tmp_path: Path) -> None:
    """With 400,000 of other savings income the next euro is in the 30% top band."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    profile = 'years:\n  2026:\n    other_savings_income_eur: "400000"\n'
    code, out = _run(tmp_path, "--db", str(db), profile=profile)
    assert code == 0
    assert "band                 30% (FIFO, top band)" in out


def test_json_output_carries_the_figures_and_labels(tmp_path: Path) -> None:
    """The JSON form has the same figures, as strings, with both labels."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db), "--json")
    payload = json.loads(out)
    assert code == 0
    assert {k: payload[k] for k in ("status", "label", "is_estimate", "is_shadow")} == {
        "status": "estimate",
        "label": "estimate · shadow (no real tax due)",
        "is_estimate": True,
        "is_shadow": True,
    }
    assert (payload["realised_gain_eur"], payload["hermes_tax_eur"]) == ("18.00", "2.57")


def test_tax_if_sold_now_uses_the_given_price(tmp_path: Path) -> None:
    """Open lot cost 101 EUR, price 201 EUR: 100 more gain, less the 4.50 carried loss, at 19%."""
    db = _ledger(tmp_path / "ledger.sqlite3", sell=False)
    code, out = _run(tmp_path, "--db", str(db), "--json", "--price", "btc=201")
    payload = json.loads(out)
    assert code == 0
    assert payload["tax_if_sold_now_eur"] == "18.15"
    assert payload["positions"] == [
        {
            "asset": "BTC",
            "unrealised_gain_eur": "100.00",
            "tax_if_sold_now_eur": "18.15",
            "pending": None,
        }
    ]


def test_missing_price_shows_a_pending_line(tmp_path: Path) -> None:
    """Without a price the open position's tax is named pending, not guessed."""
    db = _ledger(tmp_path / "ledger.sqlite3", sell=False)
    code, out = _run(tmp_path, "--db", str(db))
    assert code == 0
    assert "  BTC                tax pending: no price for BTC" in out


def test_pending_trades_print_a_labelled_pending_notice(tmp_path: Path) -> None:
    """An EUR-pending trade in the year: no figure, the reason, and the labels."""
    db = _ledger(tmp_path / "ledger.sqlite3", sell=False, priced=False)
    code, out = _run(tmp_path, "--db", str(db))
    assert (code, out) == (
        0,
        "hermes tax 2026 (estimate · shadow (no real tax due))\n"
        "tax pending: EUR values pending for 1 trade(s) in 2026\n",
    )


def test_pending_json_names_the_reason(tmp_path: Path) -> None:
    """A year without a scale is pending in JSON too, still labelled."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db), "--json", "--year", "2031")
    assert (code, json.loads(out)) == (
        0,
        {
            "year": 2031,
            "status": "pending",
            "reason": "tax pending: scale missing for 2031",
            "label": "estimate · shadow (no real tax due)",
            "is_estimate": True,
            "is_shadow": True,
        },
    )


def test_no_profile_shows_the_notice(tmp_path: Path) -> None:
    """Without a profile file the output says it assumes no other income."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db), profile=None)
    assert code == 0
    assert out.splitlines()[-1] == "notice: no taxpayer profile, assuming no other income"


def test_profile_values_are_never_logged_or_printed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The salary figure appears in no log record and not in the output (ADR-016 decision 12)."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    with caplog.at_level(logging.DEBUG):
        code, out = _run(tmp_path, "--db", str(db), "--json")
    assert code == 0
    assert "31337" not in caplog.text + out


def test_missing_ledger_fails_without_creating_one(tmp_path: Path) -> None:
    """A wrong --db path exits 1 and leaves no empty ledger behind."""
    db = tmp_path / "nope.sqlite3"
    code, out = _run(tmp_path, "--db", str(db))
    assert (code, out, db.exists()) == (1, f"ERROR: ledger not found at {db}\n", False)


def test_unreadable_ledger_exits_1(tmp_path: Path) -> None:
    """A file that is not a SQLite database is a ledger failure."""
    db = tmp_path / "ledger.sqlite3"
    db.write_text("not a database, not a database, not a database", encoding="utf-8")
    code, out = _run(tmp_path, "--db", str(db))
    assert code == 1
    assert out.startswith(f"ERROR: cannot read ledger {db}")


def test_malformed_profile_is_a_config_error(tmp_path: Path) -> None:
    """A present but broken profile fails closed with exit 3."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db), profile="years: [")
    assert code == 3
    assert out.startswith("ERROR: config load failed: Config error: invalid YAML in taxpayer")


def test_missing_config_dir_is_a_config_error(tmp_path: Path) -> None:
    """No tax_scales.yaml means exit 3."""
    out = io.StringIO()
    code = tax_module.main(
        ["--config-dir", str(tmp_path / "missing")],
        stream=out,
        now=lambda: _NOW,
        env={"HERMES_TAXPAYER_PROFILE": str(tmp_path / "taxpayer.yaml")},
    )
    assert (code, out.getvalue().startswith("ERROR: config load failed")) == (3, True)


@pytest.mark.parametrize("price", ["BTC", "=5", "BTC=abc", "BTC=-1", "BTC=inf"])
def test_bad_price_is_a_usage_error(tmp_path: Path, price: str) -> None:
    """--price needs ASSET=EUR with a finite non-negative amount."""
    code, out = _run(tmp_path, "--price", price)
    assert code == 2
    assert out.startswith("ERROR: --price expects ASSET=EUR")


def test_unknown_argument_is_a_usage_error(tmp_path: Path) -> None:
    """An unknown flag exits 2 with the usage text."""
    code, out = _run(tmp_path, "--nope")
    assert code == 2
    assert "Usage: hermes tax" in out


def test_cli_dispatches_tax_help() -> None:
    """``hermes tax --help`` is reachable from the top-level CLI."""
    out = io.StringIO()
    code = cli_module.main(["tax", "--help"], stream=out)
    assert code == 0
    assert out.getvalue().startswith("Usage: hermes tax")


def test_profile_maps_onto_the_domain_with_the_default_minimum() -> None:
    """A year without a minimum override takes the config default; other years are None."""
    profile = TaxpayerProfile.model_validate(
        {
            "years": {
                2026: {
                    "general_income_eur": "100",
                    "other_capital_gains_eur": "7",
                    "losses_carried_forward": [{"origin_year": 2024, "amount_eur": "3"}],
                }
            }
        }
    )
    mapped = tax_module.to_domain_taxpayer(profile, 2026, default_personal_minimum=Decimal("5550"))
    assert mapped == TaxpayerYear(
        general_income=Decimal("100"),
        personal_minimum=Decimal("5550"),
        other_capital_gains=Decimal("7"),
        carried_losses=(CarriedLoss(2024, Decimal("3")),),
    )
    assert tax_module.to_domain_taxpayer(profile, 2025, default_personal_minimum=Decimal(1)) is None


def test_config_scales_map_onto_the_domain() -> None:
    """The repo's 2026 scale taxes 6,000 at 1,140, as the YAML states."""
    scales = tax_module.to_domain_scales(load_tax_scales(_CONFIG_DIR))
    assert scales[2026].tax(Decimal("6000")) == Decimal("1140.00")


# ---------------------------------------------------------------------------
# Total IRPF view (change total-irpf-view R-4, R-5)
# ---------------------------------------------------------------------------

_VIEW_PROFILE = """\
show_total_irpf: true
region: murcia
years:
  2026:
    general_income_eur: "37000"
    general_deductions_eur: "2000"
"""


def test_total_irpf_view_prints_when_switched_on(tmp_path: Path) -> None:
    """Murcia, salary 37,000 less 2,000 deductions, hermes gain 18.

    General base 35,000 -> 7,343.40 (domain test_total). Savings 18 * 0.19 = 3.42.
    Total 7,346.82.
    """
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db), profile=_VIEW_PROFILE)
    assert code == 0
    assert "total IRPF (estimate, before quota deductions)" in out
    assert "  general tax   7343.40 EUR" in out
    assert "  savings tax   3.42 EUR" in out
    assert "  hermes share  3.42 EUR" in out
    assert "  total         7346.82 EUR" in out


def test_total_irpf_view_json(tmp_path: Path) -> None:
    """The JSON form carries the same four figures as strings."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db), "--json", profile=_VIEW_PROFILE)
    assert code == 0
    assert json.loads(out)["total_irpf"] == {
        "status": "estimate",
        "general_tax_eur": "7343.40",
        "savings_tax_eur": "3.42",
        "hermes_share_eur": "3.42",
        "total_eur": "7346.82",
    }


def test_view_off_by_default_prints_nothing_extra(tmp_path: Path) -> None:
    """Without show_total_irpf neither the text nor the JSON mentions total IRPF."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    _, text = _run(tmp_path, "--db", str(db))
    _, raw = _run(tmp_path, "--db", str(db), "--json")
    assert "total IRPF" not in text
    assert "total_irpf" not in json.loads(raw)


def test_hermes_tax_line_is_the_same_with_the_view_on_and_off(tmp_path: Path) -> None:
    """Switching the view on changes no hermes figure (ADR-016 item 11)."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    off_profile = _VIEW_PROFILE.replace("show_total_irpf: true", "show_total_irpf: false")
    _, on = _run(tmp_path, "--db", str(db), "--json", profile=_VIEW_PROFILE)
    _, off = _run(tmp_path, "--db", str(db), "--json", profile=off_profile)
    on_payload = json.loads(on)
    on_payload.pop("total_irpf")
    assert on_payload == json.loads(off)


@pytest.mark.parametrize(
    ("profile", "line"),
    [
        (
            "show_total_irpf: true\n",
            "total IRPF unavailable: no region in the taxpayer profile",
        ),
        (
            "show_total_irpf: true\nregion: andalucia\n",
            "total IRPF unavailable: no scale for andalucia 2026",
        ),
    ],
)
def test_missing_region_or_scale_prints_unavailable(
    tmp_path: Path, profile: str, line: str
) -> None:
    """The view fails closed with a reason, never a guessed figure."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(tmp_path, "--db", str(db), profile=profile)
    assert code == 0
    assert line in out.splitlines()


def test_missing_year_scale_is_unavailable_in_json(tmp_path: Path) -> None:
    """A config without the general tables reports the state scale missing for the year."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "tax_scales.yaml").write_text(
        'personal_minimum_eur: "5550"\nsavings:\n  2026:\n    bands: [{ rate: "0.19" }]\n',
        encoding="utf-8",
    )
    db = _ledger(tmp_path / "ledger.sqlite3")
    code, out = _run(
        tmp_path, "--db", str(db), "--json", "--config-dir", str(config_dir), profile=_VIEW_PROFILE
    )
    assert code == 0
    assert json.loads(out)["total_irpf"] == {
        "status": "unavailable",
        "reason": "total IRPF unavailable: no scale for state 2026",
    }


def test_view_logs_no_profile_value(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """With the view on, salary, deductions and region reach no log record."""
    db = _ledger(tmp_path / "ledger.sqlite3")
    with caplog.at_level(logging.DEBUG):
        code, _ = _run(tmp_path, "--db", str(db), profile=_VIEW_PROFILE)
    assert code == 0
    for secret in ("37000", "2000", "murcia"):
        assert secret not in caplog.text


def test_general_deductions_map_onto_the_domain() -> None:
    """general_deductions_eur reaches TaxpayerYear.general_deductions."""
    profile = TaxpayerProfile.model_validate(
        {"years": {2026: {"general_income_eur": "100", "general_deductions_eur": "40"}}}
    )
    mapped = tax_module.to_domain_taxpayer(profile, 2026, default_personal_minimum=Decimal("5550"))
    assert mapped is not None
    assert mapped.general_base == Decimal("60")
