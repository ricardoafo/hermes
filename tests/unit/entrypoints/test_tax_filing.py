"""End-to-end tests for ``hermes tax-filing`` (change 36-modelo-100-721 A-3.2).

Runs the command on a synthetic ledger written through the production path.
Exit codes: 0 complete, 1 ledger failure, 2 usage, 3 config, 4 incomplete.
"""

import io
import shutil
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from hermes.domain.trading.order import OrderSide
from hermes.entrypoints import cli as cli_module
from hermes.entrypoints import tax_filing
from hermes.entrypoints.commands import discover
from tests.support.paths import REPO_ROOT
from tests.unit.adapters.ledger.filing_ledger import constant_fx, record_fill, standard_ledger

_CONFIG_DIR = REPO_ROOT / "config"
_AFTER_YEAR_END = date(2027, 1, 5)


def _run(argv: list[str], tmp_path: Path, err: io.StringIO | None = None) -> tuple[int, str]:
    stream = io.StringIO()
    code = tax_filing.main(
        [*argv, "--config-dir", str(_CONFIG_DIR), "--history", str(tmp_path / "none.yaml")],
        stream=stream,
        err=err,
        fx_factory=lambda _cfg: constant_fx(),
        env={},
        today=_AFTER_YEAR_END,
    )
    return code, stream.getvalue()


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    standard_ledger(db)
    return db


def test_complete_year_exits_0_with_both_sections(tmp_path: Path) -> None:
    """Invariant: a fully priced year prints both sections with the ledger's figures."""
    code, out = _run(["--year", "2026", "--db", str(_ledger(tmp_path))], tmp_path)
    assert code == 0
    assert "| BTC | other virtual currency | 1 | 1.5 | 83916.00 | 64064.00 | 19852.00 |" in out
    assert "| BTC | A | 0.5 | 64000.00 | 32000.00 | 2026-04-01 |" in out


def test_out_dir_receives_the_report_and_csv_files(tmp_path: Path) -> None:
    """Invariant: --out writes report.md and every CSV file."""
    out_dir = tmp_path / "out"
    _run(["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out_dir)], tmp_path)
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "modelo100-sales.csv",
        "modelo100-summary.csv",
        "modelo721.csv",
        "report.md",
    ]


def test_price_override_is_used_for_the_721(tmp_path: Path) -> None:
    """Invariant: --price BTC=120000.02 values 0.5 BTC at 60,000.01 EUR, so the 721 is due."""
    code, out = _run(
        ["--year", "2026", "--db", str(_ledger(tmp_path)), "--price", "btc=120000.02"], tmp_path
    )
    assert (code, "**Obligation: required.**" in out, "local 721 history" in out) == (
        0,
        True,
        True,
    )


def test_pending_eur_values_exit_4(tmp_path: Path) -> None:
    """Invariant: a sale recorded during an FX outage makes the year incomplete (exit 4)."""
    db = _ledger(tmp_path)
    conn = sqlite3.connect(db)
    try:
        record_fill(
            conn,
            OrderSide.SELL,
            "0.1",
            "70000",
            "7",
            datetime(2026, 7, 1, 10, tzinfo=UTC),
            fx=constant_fx(days=0),
        )
    finally:
        conn.close()
    code, out = _run(["--year", "2026", "--db", str(db)], tmp_path)
    assert (code, "1 sale(s) with pending EUR values" in out) == (tax_filing.EXIT_INCOMPLETE, True)


def test_missing_ledger_exits_3_and_creates_nothing(tmp_path: Path) -> None:
    """Invariant: no ledger file exits 3 (#1187), and the command never creates one."""
    missing = tmp_path / "absent.sqlite3"
    err = io.StringIO()
    code, out = _run(["--year", "2026", "--db", str(missing)], tmp_path, err)
    assert (code, out, err.getvalue(), missing.exists()) == (
        3,
        "",
        f"ERROR: no ledger at {missing}\n",
        False,
    )


def test_missing_year_is_a_usage_error(tmp_path: Path) -> None:
    """Invariant: --year is required."""
    err = io.StringIO()
    code, out = _run(["--db", "x"], tmp_path, err)
    assert (code, out, err.getvalue().splitlines()[0]) == (2, "", "ERROR: --year is required.")


def test_bad_price_is_a_usage_error(tmp_path: Path) -> None:
    """Invariant: a malformed or unknown --price is rejected before any read."""
    err = io.StringIO()
    code, out = _run(["--year", "2026", "--price", "DOGE=1"], tmp_path, err)
    assert (code, out, err.getvalue()) == (
        2,
        "",
        "ERROR: --price expects COIN=EUR with a known coin, got 'DOGE=1'\n",
    )


def test_negative_price_is_a_usage_error(tmp_path: Path) -> None:
    """Invariant: a price below zero is rejected."""
    code, _ = _run(["--year", "2026", "--price", "BTC=-1"], tmp_path)
    assert code == 2


def test_broken_filing_config_exits_3(tmp_path: Path) -> None:
    """Invariant: a config error is exit 3, before the ledger is touched."""
    config_dir = tmp_path / "config"
    shutil.copytree(_CONFIG_DIR, config_dir)
    (config_dir / "filing.yaml").write_text("modelo721: {}\n", encoding="utf-8")
    stream, err = io.StringIO(), io.StringIO()
    code = tax_filing.main(
        ["--year", "2026", "--config-dir", str(config_dir)],
        stream=stream,
        err=err,
        fx_factory=lambda _cfg: constant_fx(),
        env={},
        today=_AFTER_YEAR_END,
    )
    assert (code, stream.getvalue(), err.getvalue().startswith("ERROR: config load failed:")) == (
        3,
        "",
        True,
    )


def test_help_exits_0(tmp_path: Path) -> None:
    """Invariant: --help prints usage and succeeds."""
    code, out = _run(["--help"], tmp_path)
    assert (code, out.splitlines()[0]) == (
        0,
        "Usage: hermes tax-filing --year YYYY [--db PATH] [--config-dir DIR] [--out DIR]",
    )


def test_command_is_registered() -> None:
    """Invariant: the registry finds `tax-filing` from its module file."""
    assert discover()["tax-filing"].name == "tax-filing"


def test_cli_without_arguments_prints_usage_and_exits_2() -> None:
    """Invariant: `hermes tax-filing` alone is a usage error."""
    stream = io.StringIO()
    code = cli_module.main(["tax-filing"], stream=stream)
    assert (code, stream.getvalue().splitlines()[0]) == (
        2,
        "Usage: hermes tax-filing --year YYYY [--db PATH] [--config-dir DIR] [--out DIR]",
    )
