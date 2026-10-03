#!/usr/bin/env python3
"""Print the trial count and Sharpe variance the Deflated Sharpe Ratio needs.

Reads the hash-chained trial log every backtest run appends to (pre-registration
§8.1) and prints JSON in the shape of the ``trials`` section of
``hermes certify`` evidence: ``{"count": N, "sharpe_variance": V}``, plus
``validation_count``. ``sharpe_variance`` is ``null`` until at least two
trials report a Sharpe ratio.

Usage:
    python scripts/backtest/trial_stats.py
    python scripts/backtest/trial_stats.py --strategy-id swing-mvp1-phase1-btcusdt

Exit codes:
    0 - summary printed
    1 - the trial log is broken or unreadable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.strategy_protocol.trial_log import trial_summary
from hermes.domain.errors import TrialLogError

DEFAULT_LOG = Path("docs/experiments/swing-mvp1/trial-log.jsonl")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and print the summary."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trial-log", type=Path, default=DEFAULT_LOG, help="Trial log path")
    parser.add_argument("--strategy-id", default=None, help="Only count this strategy's trials")
    args = parser.parse_args(argv)
    try:
        summary = trial_summary(JsonlTrialLog(args.trial_log), strategy_id=args.strategy_id)
    except TrialLogError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "count": summary.count,
                "validation_count": summary.validation_count,
                "sharpe_variance": summary.sharpe_variance,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
