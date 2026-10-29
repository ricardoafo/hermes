"""Tests for the Binance fee-history CSV loader."""

from datetime import date

from hermes.adapters.fees.binance_history import load_binance_fee_history
from tests.support.paths import REPO_ROOT

CSV_PATH = REPO_ROOT / "docs" / "experiments" / "swing-mvp1" / "fee-history.csv"


def test_load_returns_expected_row_count() -> None:
    """The real CSV parses into the expected Binance-only row count (A-3.1).

    The reconstructed history has 9 Binance rows after filtering out the
    informational ``VIP0_BTCBUSD`` line.
    """
    rows = load_binance_fee_history(CSV_PATH)
    assert len(rows) == 9, (
        f"expected 9 Binance rows after VIP0_BTCBUSD filter, got {len(rows)}: "
        f"{[(r.effective_date, r.tier) for r in rows]}"
    )


def test_zero_fee_btc_row_is_present() -> None:
    """The 2022-07-28 zero-fee BTC row (VIP0, 0/0) must be present (A-3.2)."""
    rows = load_binance_fee_history(CSV_PATH)
    matches = [
        r
        for r in rows
        if r.effective_date == date(2022, 7, 28) and r.tier == "VIP0" and r.maker_bps == 0
    ]
    assert len(matches) == 1, (
        f"expected exactly one 2022-07-28 VIP0 zero-fee row, got {len(matches)}: {matches}"
    )
    row = matches[0]
    assert row.taker_bps == 0, f"zero-fee BTC taker must be 0, got {row.taker_bps}"


def test_eth_sol_override_row_is_present() -> None:
    """The 2022-07-28 VIP0_ETH_SOL override row (10/10) must be present too."""
    rows = load_binance_fee_history(CSV_PATH)
    matches = [
        r for r in rows if r.effective_date == date(2022, 7, 28) and r.tier == "VIP0_ETH_SOL"
    ]
    assert len(matches) == 1, (
        f"expected exactly one 2022-07-28 VIP0_ETH_SOL override row, got {len(matches)}"
    )
    assert matches[0].maker_bps == 10, "ETH/SOL override maker must be 10 bps"
    assert matches[0].taker_bps == 10, "ETH/SOL override taker must be 10 bps"


def test_btcbusd_row_filtered_out() -> None:
    """The informational VIP0_BTCBUSD row (BUSD phased out) must NOT appear (A-3.3)."""
    rows = load_binance_fee_history(CSV_PATH)
    tiers = {r.tier for r in rows}
    assert "VIP0_BTCBUSD" not in tiers, f"BTCBUSD row should be filtered, got tiers: {tiers}"


def test_all_returned_rows_are_binance() -> None:
    """Non-Binance rows (future Kraken addition) are filtered by the loader."""
    rows = load_binance_fee_history(CSV_PATH)
    # The loader doesn't retain the exchange field, but the row set only
    # includes tiers known to be Binance. If a future CSV row uses a
    # tier name we don't recognise, this test still passes — we're
    # checking that non-Binance rows don't leak, not policing tier names.
    # As a sanity check, every returned row must have a tier that starts
    # with "VIP" (Binance-style tier naming).
    for r in rows:
        assert r.tier.startswith("VIP"), (
            f"non-Binance-shaped tier '{r.tier}' leaked into the row set "
            f"(effective_date={r.effective_date})"
        )
