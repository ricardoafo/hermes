"""End-to-end tests for ``hermes tax-pack`` (change 568-tax-pack R-1, R-4).

Runs the command on a synthetic ledger written through the production path,
with a constant fake FX rate and a fixed clock. Exit codes: 0 written,
1 ledger failure, 2 usage, 3 config, 5 frozen.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from hermes.domain.trading.order import OrderSide
from hermes.entrypoints import tax_pack
from hermes.entrypoints.commands import discover
from tests.unit.adapters.ledger.filing_ledger import constant_fx, record_fill, standard_ledger

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _PROJECT_ROOT / "config"
_DURING_YEAR = datetime(2026, 10, 4, 5, 30, tzinfo=UTC)
_AFTER_YEAR_END = datetime(2027, 1, 5, 6, 30, tzinfo=UTC)


@dataclass
class FakeEstimate:
    """Stands in for ``hermes tax``; the real command is tested in test_tax.py."""

    text: str | None = "hermes tax 2026 (estimate)\n"
    calls: list[list[str]] = field(default_factory=list)

    def __call__(
        self, argv: list[str], _env: Mapping[str, str], _now: Callable[[], datetime]
    ) -> str | None:
        """Record the arguments and return the scripted text."""
        self.calls.append(argv)
        return self.text


def _config_with_2026_boxes(tmp_path: Path) -> Path:
    """Copy the repo config and confirm the 2026 Renta Web boxes, as each March's PR does."""
    config = tmp_path / "config"
    shutil.copytree(_CONFIG_DIR, config)
    with (config / "renta_campaigns.yaml").open("a", encoding="utf-8") as handle:
        handle.write(
            '  2026:\n    transmissions_box: "1804"\n    source: "test"\n'
            '    verified_on: "2027-04-01"\n'
        )
    return config


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    standard_ledger(db)
    return db


def _run(
    argv: list[str],
    tmp_path: Path,
    *,
    at: datetime = _DURING_YEAR,
    env: dict[str, str] | None = None,
    estimate: FakeEstimate | None = None,
    config_dir: Path = _CONFIG_DIR,
) -> tuple[int, str]:
    stream = io.StringIO()
    code = tax_pack.main(
        [*argv, "--config-dir", str(config_dir), "--history", str(tmp_path / "none.yaml")],
        stream=stream,
        fx_factory=lambda _cfg: constant_fx(),
        env=env if env is not None else {},
        now=lambda: at,
        estimate_runner=estimate if estimate is not None else FakeEstimate(),
    )
    return code, stream.getvalue()


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def test_pack_folder_holds_every_file(tmp_path: Path) -> None:
    """Invariant (A-1.1): one refresh writes the whole pack under <out>/<year>/."""
    out = tmp_path / "packs"
    code, _ = _run(["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)], tmp_path)
    assert code == 0
    assert sorted(p.name for p in (out / "2026").iterdir()) == [
        "README.md",
        "changes.md",
        "dossier.html",
        "figures.json",
        "manifest.json",
        "modelo100-sales.csv",
        "modelo100-summary.csv",
        "modelo721.csv",
        "renta-web.html",
        "report.md",
        "trail.csv",
    ]


def test_manifest_matches_every_file_written(tmp_path: Path) -> None:
    """Invariant (A-1.2): each manifest checksum is the sha256 of the file on disk."""
    out = tmp_path / "packs"
    _run(["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)], tmp_path)
    directory = out / "2026"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    on_disk = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.iterdir()
        if p.name != "manifest.json"
    }
    assert manifest["sha256"] == on_disk


def test_trail_matches_the_ledger_sale(tmp_path: Path) -> None:
    """Invariant (R-2): the trail's lines of the 1.5 BTC sale add up to its 19,852 EUR gain."""
    out = tmp_path / "packs"
    _run(["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)], tmp_path)
    rows = (out / "2026" / "trail.csv").read_text(encoding="utf-8").splitlines()[1:]
    gains = [float(row.split(",")[10]) for row in rows]
    assert (len(rows), round(sum(gains), 2)) == (2, 19852.00)


def test_refresh_without_new_trades_appends_no_change(tmp_path: Path) -> None:
    """Invariant (A-4.1): rerunning on the same ledger leaves changes.md as it was."""
    out = tmp_path / "packs"
    argv = ["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)]
    _run(argv, tmp_path)
    before = (out / "2026" / "changes.md").read_text(encoding="utf-8")
    _run(argv, tmp_path)
    assert (out / "2026" / "changes.md").read_text(encoding="utf-8") == before


def test_a_new_sale_is_logged_in_changes(tmp_path: Path) -> None:
    """Invariant (A-4.1): a sale between refreshes appends the moved figures."""
    out = tmp_path / "packs"
    db = _ledger(tmp_path)
    argv = ["--year", "2026", "--db", str(db), "--out", str(out)]
    _run(argv, tmp_path)
    conn = sqlite3.connect(db)
    record_fill(conn, OrderSide.SELL, "0.5", "75000", "0", datetime(2026, 9, 1, 10, tzinfo=UTC))
    conn.close()
    code, printed = _run(argv, tmp_path)
    changes = (out / "2026" / "changes.md").read_text(encoding="utf-8")
    assert code == 0
    assert "- sales: 1 → 2" in changes
    assert "changed: sales: 1 → 2" in printed


def test_ready_closed_year_freezes_and_is_never_rewritten(tmp_path: Path) -> None:
    """Invariant (A-4.2): a closed, complete year writes FROZEN; the next run exits 5 untouched."""
    out = tmp_path / "packs"
    argv = ["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)]
    config = _config_with_2026_boxes(tmp_path)
    code, _ = _run(argv, tmp_path, at=_AFTER_YEAR_END, config_dir=config)
    assert code == 0
    assert (out / "2026" / "FROZEN").is_file()
    before = _snapshot(out / "2026")
    code, printed = _run(argv, tmp_path, at=_AFTER_YEAR_END, config_dir=config)
    assert (code, _snapshot(out / "2026")) == (tax_pack.EXIT_FROZEN, before)
    assert "Tax pack 2026 is frozen; nothing written." in printed


def test_open_year_is_not_frozen(tmp_path: Path) -> None:
    """Invariant (D-2): during the year the pack refreshes and never freezes."""
    out = tmp_path / "packs"
    _run(["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)], tmp_path)
    assert not (out / "2026" / "FROZEN").exists()


def test_without_year_refreshes_this_and_last_year_and_skips_a_frozen_one(tmp_path: Path) -> None:
    """Invariant (D-3): no --year means last year (unless frozen) and this year."""
    out = tmp_path / "packs"
    argv = ["--db", str(_ledger(tmp_path)), "--out", str(out)]
    config = _config_with_2026_boxes(tmp_path)
    assert _run(argv, tmp_path, at=_AFTER_YEAR_END, config_dir=config)[0] == 0
    assert sorted(p.name for p in out.iterdir()) == ["2026", "2027"]
    code, printed = _run(argv, tmp_path, at=_AFTER_YEAR_END, config_dir=config)
    assert code == 0
    assert "Tax pack 2026 is frozen; nothing written." in printed
    assert "Tax pack 2027: open." in printed


def test_estimate_is_added_only_where_the_profile_exists(tmp_path: Path) -> None:
    """Invariant (D-4): with a profile file the `hermes tax` text is written; without, never run."""
    out = tmp_path / "packs"
    db = _ledger(tmp_path)
    argv = ["--year", "2026", "--db", str(db), "--out", str(out)]
    absent = FakeEstimate()
    _run(
        argv, tmp_path, env={"HERMES_TAXPAYER_PROFILE": str(tmp_path / "no.yaml")}, estimate=absent
    )
    assert (absent.calls, (out / "2026" / "tax-estimate.txt").exists()) == ([], False)
    profile = tmp_path / "taxpayer.yaml"
    profile.write_text("years: {}\n", encoding="utf-8")
    present = FakeEstimate()
    _run(argv, tmp_path, env={"HERMES_TAXPAYER_PROFILE": str(profile)}, estimate=present)
    assert present.calls == [["--year", "2026", "--db", str(db), "--config-dir", str(_CONFIG_DIR)]]
    assert (out / "2026" / "tax-estimate.txt").read_text(encoding="utf-8") == present.text


def test_a_failed_estimate_still_writes_the_pack(tmp_path: Path) -> None:
    """Invariant: `hermes tax` failing warns and leaves the filing figures written."""
    out = tmp_path / "packs"
    profile = tmp_path / "taxpayer.yaml"
    profile.write_text("years: {}\n", encoding="utf-8")
    code, printed = _run(
        ["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)],
        tmp_path,
        env={"HERMES_TAXPAYER_PROFILE": str(profile)},
        estimate=FakeEstimate(text=None),
    )
    assert code == 0
    assert "WARNING: `hermes tax` failed" in printed
    assert not (out / "2026" / "tax-estimate.txt").exists()


def test_out_defaults_to_the_environment_folder(tmp_path: Path) -> None:
    """Invariant (D-5): without --out the pack goes to $HERMES_TAX_PACK_DIR."""
    folder = tmp_path / "env-packs"
    _run(
        ["--year", "2026", "--db", str(_ledger(tmp_path))],
        tmp_path,
        env={tax_pack.PACK_DIR_ENV: str(folder)},
    )
    assert (folder / "2026" / "README.md").is_file()


def test_missing_ledger_exits_1(tmp_path: Path) -> None:
    """Invariant: no ledger file is a ledger failure and writes no pack."""
    out = tmp_path / "packs"
    code, printed = _run(
        ["--year", "2026", "--db", str(tmp_path / "missing.sqlite3"), "--out", str(out)], tmp_path
    )
    assert (code, out.exists()) == (1, False)
    assert "ERROR: no ledger at" in printed


def test_bad_price_exits_2(tmp_path: Path) -> None:
    """Invariant: a malformed --price is a usage error."""
    code, _ = _run(["--year", "2026", "--price", "BTC"], tmp_path)
    assert code == 2


def test_bad_config_dir_exits_3(tmp_path: Path) -> None:
    """Invariant: a config that cannot load is exit 3, before the ledger is read."""
    stream = io.StringIO()
    code = tax_pack.main(
        ["--year", "2026", "--config-dir", str(tmp_path / "nowhere")],
        stream=stream,
        fx_factory=lambda _cfg: constant_fx(),
        env={},
        now=lambda: _DURING_YEAR,
    )
    assert code == 3


def test_help_exits_0_with_usage(tmp_path: Path) -> None:
    """Invariant: --help prints the usage and the exit codes."""
    code, printed = _run(["--help"], tmp_path)
    assert code == 0
    assert "5 frozen" in printed


def test_command_is_registered() -> None:
    """Invariant: `hermes tax-pack` is discovered by the CLI registry."""
    assert discover()["tax-pack"].name == "tax-pack"


def test_closed_year_waits_for_its_own_renta_boxes(tmp_path: Path) -> None:
    """Invariant (D-6): with only last campaign's boxes, a closed year stays unfrozen."""
    out = tmp_path / "packs"
    _run(
        ["--year", "2026", "--db", str(_ledger(tmp_path)), "--out", str(out)],
        tmp_path,
        at=_AFTER_YEAR_END,
    )
    assert not (out / "2026" / "FROZEN").exists()
