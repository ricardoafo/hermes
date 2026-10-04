"""Tests for the tax pack builder (change 568-tax-pack R-1 to R-4).

Uses the scripted reader; the same seam against a real ledger file is covered
in ``tests/unit/entrypoints/test_tax_pack.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from hermes.application.filing.pack import (
    ESTIMATE_FILE,
    FIGURES_FILE,
    PackOutput,
    build_pack_output,
    collect_trail,
    render_frozen_marker,
    render_manifest,
)
from hermes.application.filing.service import Custodian, FilingPack, build_filing_pack
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.filing.modelo100 import DisposalPiece
from hermes.domain.filing.modelo721 import Modelo721Rules
from hermes.domain.filing.pack_changes import FIRST_REFRESH
from hermes.domain.filing.readiness import PackStatus
from hermes.domain.filing.trail import TradeRate
from hermes.domain.value_objects.currency import Currency
from tests.unit.application.filing.fake_reader import FakeFilingReader
from tests.unit.application.ledger.fake_fx import FakeFx

_RULES = Modelo721Rules(threshold_eur=Decimal("50000"), increase_eur=Decimal("20000"))
_DEC31 = date(2026, 12, 31)
_OPEN_DAY = date(2026, 10, 4)
_CLOSED_DAY = date(2027, 1, 5)
_NOW = datetime(2026, 10, 4, 5, 30, tzinfo=UTC)
_SALE_AT = datetime(2026, 6, 1, 10, tzinfo=UTC)


@dataclass
class FakeRates:
    """Answers a fixed rate table."""

    rates: dict[str, TradeRate] = field(default_factory=dict)

    def eur_rates(self) -> dict[str, TradeRate]:
        """Return the scripted rates."""
        return dict(self.rates)


def _sale(ref: str = "3", at: datetime = _SALE_AT, fee: str | None = "10") -> DisposalPiece:
    return DisposalPiece(
        sell_ref=ref,
        asset=Currency.BTC,
        received=Currency.USDT,
        disposed_at=at,
        buy_ref="1",
        acquired_at=datetime(2026, 3, 2, 10, tzinfo=UTC),
        qty=Decimal("1"),
        proceeds_eur=Decimal("1000"),
        cost_eur=Decimal("800"),
        fee_eur=None if fee is None else Decimal(fee),
    )


def _filing(reader: FakeFilingReader, today: date) -> FilingPack:
    return build_filing_pack(
        2026,
        reader,
        EurPricer(FakeFx(rates={_DEC31: (Decimal("1.25"), _DEC31)})),
        rules=_RULES,
        custodian=Custodian(name="Exchange", country=None),
        previous=None,
        overrides={},
        today=today,
        shadow=True,
    ).unwrap()


def _output(
    *,
    pieces: list[DisposalPiece] | None = None,
    today: date = _OPEN_DAY,
    estimate: str | None = None,
    previous: dict[str, str] | None = None,
) -> PackOutput:
    reader = FakeFilingReader(pieces=[_sale()] if pieces is None else pieces)
    pack = _filing(reader, today)
    trail = collect_trail(2026, reader, FakeRates())
    return build_pack_output(
        pack,
        trail,
        estimate=estimate,
        previous_figures=previous,
        today=today,
        refreshed_at=_NOW,
    )


def test_pack_holds_the_filing_report_csvs_trail_and_figures() -> None:
    """Invariant: every refresh writes the report, the three CSV files, the trail and figures."""
    assert sorted(_output().files) == [
        "README.md",
        FIGURES_FILE,
        "modelo100-sales.csv",
        "modelo100-summary.csv",
        "modelo721.csv",
        "report.md",
        "trail.csv",
    ]


def test_estimate_is_written_only_when_given() -> None:
    """Invariant (D-4): the estimate file exists only where an estimate was produced."""
    assert _output(estimate="hermes tax 2026\n").files[ESTIMATE_FILE] == "hermes tax 2026\n"
    assert ESTIMATE_FILE not in _output().files


def test_figures_carry_the_modelo100_net() -> None:
    """Invariant: figures.json holds the net the report prints (990 - 800 = 190)."""
    figures = json.loads(_output().files[FIGURES_FILE])
    assert figures["Modelo 100 net (EUR)"] == "190.00"


def test_unchanged_figures_add_no_changes_entry() -> None:
    """Invariant (A-4.1): a refresh that moves nothing appends nothing to changes.md."""
    figures = json.loads(_output().files[FIGURES_FILE])
    again = _output(previous=figures)
    assert (again.changes, again.changes_entry) == ((), None)


def test_a_new_sale_appears_in_the_changes_entry() -> None:
    """Invariant (A-4.1): a new sale is reported as changed figures with the refresh time."""
    figures = json.loads(_output().files[FIGURES_FILE])
    later = _output(
        pieces=[_sale(), _sale("4", at=datetime(2026, 9, 1, 10, tzinfo=UTC))], previous=figures
    )
    assert later.changes_entry is not None
    assert later.changes_entry.startswith("## 2026-10-04 07:30 Madrid\n")
    assert "- Modelo 100 net (EUR): 190.00 → 380.00" in later.changes_entry


def test_first_refresh_is_recorded() -> None:
    """Invariant: the first refresh leaves a dated entry saying so."""
    assert _output().changes == (FIRST_REFRESH,)


def test_open_year_is_not_frozen() -> None:
    """Invariant (D-2): a year still in progress is never frozen."""
    output = _output()
    assert (output.readiness.status, output.freeze) == (PackStatus.OPEN, False)


def test_closed_ready_year_is_frozen() -> None:
    """Invariant (D-2): a closed year with nothing blocking freezes."""
    output = _output(today=_CLOSED_DAY)
    assert (output.readiness.status, output.freeze) == (PackStatus.READY, True)


def test_closed_year_with_a_pending_sale_is_not_frozen() -> None:
    """Invariant (D-2): pending EUR values keep a closed year open to refreshes."""
    output = _output(pieces=[_sale(fee=None)], today=_CLOSED_DAY)
    assert (output.readiness.status, output.freeze) == (PackStatus.INCOMPLETE, False)


def test_trail_only_holds_the_years_sales() -> None:
    """Invariant: the trail reads the same Madrid tax year as the report."""
    late = _sale("9", at=datetime(2026, 12, 31, 23, 30, tzinfo=UTC))
    trail = collect_trail(2026, FakeFilingReader(pieces=[_sale(), late]), FakeRates())
    assert [line.sell_ref for line in trail] == ["3"]


def test_trail_csv_lines_carry_values_and_rates() -> None:
    """Invariant: a trail row shows the EUR values, gain and both trades' rates."""
    reader = FakeFilingReader(pieces=[_sale()])
    rates = FakeRates(
        {
            "3": TradeRate(rate=Decimal("1.25"), rate_date=date(2026, 6, 1)),
            "1": TradeRate(rate=Decimal("1.20"), rate_date=date(2026, 3, 2)),
        }
    )
    output = build_pack_output(
        _filing(reader, _OPEN_DAY),
        collect_trail(2026, reader, rates),
        estimate=None,
        previous_figures=None,
        today=_OPEN_DAY,
        refreshed_at=_NOW,
    )
    row = output.files["trail.csv"].splitlines()[1]
    assert row == (
        "3,2026-06-01,BTC,USDT,1,2026-03-02,1,1000.00,10.00,800.00,190.00,"
        "1.25,2026-06-01,1.20,2026-03-02"
    )


def test_readme_shows_status_checklist_and_shadow_label() -> None:
    """Invariant: the summary leads with the estimate label, the status and the checklist."""
    readme = _output().files["README.md"]
    assert "**Status: open.**" in readme
    assert "Hermes submits nothing to AEAT" in readme
    assert "- [ ] Tax year 2026 is still open" in readme
    assert "Shadow (simulated fills, no real tax due)" in readme


def test_manifest_hashes_every_file() -> None:
    """Invariant (A-1.2): the manifest's sha256 of each file matches its bytes."""
    contents = {"a.txt": b"one", "b.txt": b"two"}
    manifest = json.loads(render_manifest(2026, _NOW, contents))
    assert manifest["sha256"] == {
        "a.txt": hashlib.sha256(b"one").hexdigest(),
        "b.txt": hashlib.sha256(b"two").hexdigest(),
    }


def test_frozen_marker_says_how_to_reopen() -> None:
    """Invariant: the marker names the step that reopens a frozen year."""
    assert "delete this file and run `hermes tax-pack --year 2026`" in render_frozen_marker(
        2026, _NOW
    )
