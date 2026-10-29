"""Load the Binance point-in-time fee history from the pre-reg CSV.

Parses ``docs/experiments/swing-mvp1/fee-history.csv`` into a list of
:class:`~hermes.domain.fees.fee_schedule_selector.FeeScheduleRow`
records. Only Binance rows are returned; the loader filters out other
exchanges and the informational ``VIP0_BTCBUSD`` rows (BUSD was phased
out via the Paxos/NYDFS action Feb 2023 and is not a tracked pair).

Reference:
    - openspec/archive/fee-history-time-aware-wire/change.md
    - docs/experiments/swing-mvp1/fee-history.csv
    - docs/experiments/swing-mvp1/pre-registration-v1.md §6.1
"""

import csv
from datetime import date
from pathlib import Path

from hermes.domain.fees.fee_schedule_selector import FeeScheduleRow

__all__ = ["load_binance_fee_history"]


def load_binance_fee_history(csv_path: Path) -> list[FeeScheduleRow]:
    """Return every Binance fee-schedule row from the CSV.

    Filters out:

    - Rows whose ``exchange`` column is not ``"binance"`` (case-insensitive).
    - Rows whose ``tier`` is ``VIP0_BTCBUSD`` (informational only —
      BUSD was phased out before the pre-reg backtest window matters).

    Args:
        csv_path: Path to ``fee-history.csv``.

    Returns:
        List of :class:`FeeScheduleRow` — order matches file order; the
        selector re-sorts by ``effective_date`` at query time.

    Raises:
        FileNotFoundError: If ``csv_path`` does not exist.
        KeyError: If the CSV is missing an expected column.
        ValueError: If a row has an unparseable ``effective_date`` /
            ``maker_bps`` / ``taker_bps``.
    """
    rows: list[FeeScheduleRow] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            if r["exchange"].strip().lower() != "binance":
                continue
            if r["tier"].strip() == "VIP0_BTCBUSD":
                continue
            rows.append(
                FeeScheduleRow(
                    effective_date=date.fromisoformat(r["effective_date"].strip()),
                    tier=r["tier"].strip(),
                    maker_bps=int(r["maker_bps"]),
                    taker_bps=int(r["taker_bps"]),
                )
            )
    return rows
