"""``hermes shadow-start``: check shadow Ready, archive the warm-up ledger, record day 0.

The Code Owner runs this on the VPS with ``hermes.service`` stopped (agents
never run it). It:

1. Checks the ADR-021 §2 Ready rules a program can check (signed
   pre-registration hash in the lab notebook, backtest gate recorded for the
   parameters about to run, one enabled strategy) and lists the ones only the
   Code Owner can confirm.
2. Without ``--confirm START``, prints what it would do and changes nothing.
3. With it, first proves it can write every directory it will change (the
   ledger's, the archive's and the run records'), so a start never stops
   halfway; then copies the ledger read-only into the archive directory, checks
   the copy, replaces the ledger with a fresh, empty one, and writes the
   day-0 record (engine version, config, pre-registration and data manifest
   hashes) next to the ledger. It prints a lab notebook entry to commit.

Forms: ``hermes shadow-start <strategy_id> [--db PATH] [--config-dir DIR]
[--archive-dir DIR] [--confirm START]``.

Exit codes: 0 done (or dry run passed), 1 a Ready rule failed or the service
is running, 2 usage, 3 config, store or file error.

Reference: docs/architecture/adr-021-strategy-lifecycle-ready-and-done.md §2,
§3; issue #797; openspec/changes/797-shadow-day-zero/change.md.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.ledger.connection import open_ledger
from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.application.strategy_protocol.day_zero import (
    MANUAL_RULES,
    DayZeroRecord,
    Verdict,
    certification_check,
    notebook_check,
    ready,
    roster_check,
    sha256_file,
    sha256_tree,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._release import DEFAULT_RELEASE_LINK, read_release
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config

__all__ = ["CONFIRM_WORD", "main", "run"]

CONFIRM_WORD: Final = "START"
_SERVICE: Final = "hermes.service"
_READ_ONLY_FILE: Final = 0o444
_READ_ONLY_DIR: Final = 0o555

_USAGE = (
    "Usage: hermes shadow-start <strategy_id> [--db PATH] [--config-dir DIR]\n"
    "                           [--archive-dir DIR] [--confirm START]\n"
    "\n"
    "Check shadow Ready (ADR-021), then with --confirm START archive the warm-up\n"
    "ledger read-only, replace it with an empty one, and record day 0.\n"
    "Stop hermes.service first. Without --confirm it changes nothing.\n"
    "Exit codes: 0 done, 1 not ready or service running, 2 usage, 3 error.\n"
)


def service_active() -> bool:
    """Return whether systemd reports ``hermes.service`` active; ``False`` without systemd."""
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return False
    result = subprocess.run(  # noqa: S603 — systemctl resolved via shutil.which, fixed arguments
        [systemctl, "is-active", "--quiet", _SERVICE], check=False
    )
    return result.returncode == 0


def run(
    strategy_id: str,
    *,
    db: Path,
    config_dir: Path,
    archive_dir: Path,
    confirm: bool,
    stream: TextIO,
    err: TextIO,
    pre_reg: PreRegistration | None = None,
    release_link: Path = DEFAULT_RELEASE_LINK,
    is_service_active: Callable[[], bool] = service_active,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Check Ready and, when confirmed, archive the ledger and record day 0.

    Args:
        strategy_id: The strategy whose counted shadow run starts.
        db: The ledger serve uses.
        config_dir: The config serve uses.
        archive_dir: Where the read-only ledger copy goes.
        confirm: True when the Code Owner passed ``--confirm START``.
        stream: Where the checks and the notebook entry go.
        err: Where ERROR lines go.
        pre_reg: The signed pre-registration; the deployed one when ``None``.
        release_link: The ``current`` symlink naming the live release.
        is_service_active: Whether serve is running.
        clock: The current UTC time.

    Returns:
        The exit code.
    """
    try:
        signed = pre_reg if pre_reg is not None else serve_module.pre_registration_for(config_dir)
        config = load_config(config_dir)
        fingerprint = serve_module.parameter_fingerprint_for(config)
        record = JsonCertificationStore(serve_module.certification_dir_for(db)).load(strategy_id)
        pre_reg_file = signed.pre_reg_path()
        experiment = pre_reg_file.parent
        checks = (
            notebook_check(pre_reg_file, experiment / "lab-notebook.md"),
            certification_check(record, fingerprint),
            *MANUAL_RULES,
            roster_check([s.name for s in config.strategies.strategies if s.enabled], strategy_id),
        )
    except (ConfigError, serve_module.StartupError, OSError, ValueError) as exc:
        err.write(f"ERROR: {exc}\n")
        return 3

    stream.writelines(
        f"rule {c.rule} {c.verdict.value:>15}: {c.name} ({c.detail})\n"
        for c in sorted(checks, key=lambda c: c.rule)
    )
    if not ready(checks):
        err.write("ERROR: shadow is not Ready; nothing changed\n")
        return 1
    if is_service_active():
        err.write(f"ERROR: {_SERVICE} is running; stop it first. Nothing changed\n")
        return 1

    now = clock()
    run_id = f"{strategy_id}-{now:%Y%m%dT%H%M%SZ}"
    archived = archive_dir / run_id / db.name
    runs_dir = db.parent / "runs"
    if not confirm:
        stream.write(
            f"Dry run. With --confirm {CONFIRM_WORD} this would copy {db} to {archived} "
            f"(read-only), replace {db} with an empty ledger, "
            f"and write {runs_dir / (run_id + '.json')}.\n"
        )
        return 0

    try:
        _check_writable(db.parent, archived.parent.parent, runs_dir)
        archived_sha = _archive(db, archived) if db.exists() else None
        day_zero = DayZeroRecord(
            run_id=run_id,
            strategy_id=strategy_id,
            day_zero_at=now,
            package_version=_package_version(),
            release=read_release(release_link).name,
            config_sha256=sha256_tree(config_dir),
            preregistration_sha256=sha256_file(pre_reg_file),
            preregistration_signed_sha256=_signed_hash(pre_reg_file),
            data_manifest_sha256=sha256_file(experiment / "data-manifest.json"),
            parameter_fingerprint=fingerprint,
            archived_ledger=None if archived_sha is None else str(archived),
            archived_ledger_sha256=archived_sha,
            checks=tuple(sorted(checks, key=lambda c: c.rule)),
        )
        record_path = _write_record(runs_dir, day_zero)
        _remove_ledger(db)
        _create_ledger(db)
    except (OSError, sqlite3.Error, ValueError) as exc:
        err.write(f"ERROR: {exc}\n")
        return 3

    stream.write(f"Day 0 recorded: {record_path}\n")
    if archived_sha is not None:
        stream.write(f"Warm-up ledger archived read-only: {archived}\n")
    stream.write(f"Start the service; serve runs on the fresh ledger at {db}.\n\n")
    stream.write(_notebook_entry(day_zero, record_path))
    return 0


def _check_writable(*dirs: Path) -> None:
    """Prove each directory, or its nearest existing parent, takes a new file.

    A real write, not a mode-bit check: it also catches a read-only mount and
    an account that does not own the directory, so a start that would stop
    halfway (archive taken, ledger left in place) changes nothing instead.

    Raises:
        OSError: A directory refuses the probe file, naming it.
    """
    for wanted in dirs:
        target = next(d for d in (wanted, *wanted.parents) if d.exists())
        try:
            fd, probe = tempfile.mkstemp(prefix=".shadow-start-", dir=target)
            os.close(fd)
            Path(probe).unlink()
        except OSError as exc:
            raise OSError(exc.errno, f"cannot write in {target}: {exc.strerror}") from exc


def _archive(db: Path, target: Path) -> str:
    """Copy ``db`` to ``target`` with SQLite's backup API, verify it, make it read-only.

    Raises:
        ValueError: The copy fails its integrity check or differs in rows.
        OSError: The copy cannot be written.
    """
    target.parent.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"archived copy {target} fails its integrity check")
            if _row_counts(source) != _row_counts(copy):
                raise ValueError(f"archived copy {target} differs from {db} in rows")
        finally:
            copy.close()
    finally:
        source.close()
    target.chmod(_READ_ONLY_FILE)
    target.parent.chmod(_READ_ONLY_DIR)
    return sha256_file(target)


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    # Table names come from sqlite_master, never from input.
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}  # noqa: S608


def _write_record(runs_dir: Path, record: DayZeroRecord) -> Path:
    """Write the day-0 record read-only; refuse to overwrite one."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{record.run_id}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _READ_ONLY_FILE)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(record.to_json())
    return path


def _remove_ledger(db: Path) -> None:
    """Remove the ledger and its WAL and shared-memory files."""
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        path.unlink(missing_ok=True)


def _create_ledger(db: Path) -> None:
    """Create the empty ledger day 0 starts on.

    Serve refuses a missing ledger (#876), so the fresh run needs one to
    exist; serve records the capital seed on its first start.
    """
    with closing(open_ledger(db)) as connection:
        apply_migrations(connection)


def _package_version() -> str:
    try:
        return metadata.version("hermes")
    except metadata.PackageNotFoundError:
        return "unknown"


def _signed_hash(pre_reg_path: Path) -> str:
    for line in pre_reg_path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "content_sha256":
            return value.strip()
    raise ValueError(f"{pre_reg_path} has no content_sha256")


def _notebook_entry(record: DayZeroRecord, record_path: Path) -> str:
    """Return the lab notebook entry the Code Owner commits for day 0."""
    stamp = record.day_zero_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    manual = ", ".join(str(c.rule) for c in record.checks if c.verdict is Verdict.MANUAL)
    archived = (
        "none"
        if record.archived_ledger is None
        else f"{record.archived_ledger} sha256 {record.archived_ledger_sha256}"
    )
    return (
        f"### {stamp} | shadow | operator | shadow day 0: counted run {record.run_id} starts\n"
        "\n**Observed metrics**:\n\n"
        f"- Engine: hermes {record.package_version}, release {record.release}\n"
        f"- Config sha256 {record.config_sha256}\n"
        f"- Pre-registration sha256 {record.preregistration_sha256} "
        f"(signed {record.preregistration_signed_sha256})\n"
        f"- Data manifest sha256 {record.data_manifest_sha256}\n"
        f"- Parameter fingerprint {record.parameter_fingerprint}\n"
        f"- Warm-up ledger archived: {archived}\n"
        "\n**Decision**:\n\n"
        f"- Every ADR-021 §2 Ready rule holds; rules {manual} confirmed by the operator.\n"
        f"- The 90-day clock starts at the first bar after {stamp}.\n"
        "\n**Rationale**:\n\n"
        "- ADR-021 §2 and §3: the warm-up run since 2026-10-04 does not count; day 0 is the first\n"
        "  bar after every Ready rule holds.\n"
        "\n**Files touched**:\n\n"
        f"- {record_path} (VPS, added)\n"
        "- docs/experiments/swing-mvp1/lab-notebook.md (modified)\n"
    )


def _parse(argv: list[str], err: TextIO) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="hermes shadow-start", add_help=False)
    parser.add_argument("strategy_id", nargs="?")
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--confirm", default="")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed, unknown = parser.parse_known_args(argv)
    except SystemExit:
        err.write(_USAGE)
        return None
    if unknown or parsed.help or not parsed.strategy_id:
        err.write(_USAGE)
        return None
    if parsed.confirm not in ("", CONFIRM_WORD):
        err.write(f"ERROR: --confirm takes exactly {CONFIRM_WORD}\n")
        return None
    return parsed


def main(argv: list[str] | None = None, *, stream: TextIO, err: TextIO | None = None) -> int:
    """Run ``hermes shadow-start`` with ``argv``."""
    err_stream = resolve_err(err)
    parsed = _parse(list(argv or []), err_stream)
    if parsed is None:
        return 2
    db: Path = parsed.db
    return run(
        parsed.strategy_id,
        db=db,
        config_dir=parsed.config_dir,
        archive_dir=parsed.archive_dir or db.parent / "archive",
        confirm=parsed.confirm == CONFIRM_WORD,
        stream=stream,
        err=err_stream,
    )
