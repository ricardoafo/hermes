"""Shadow day 0: the Ready check and the record that starts a counted shadow run.

ADR-021 §2 says shadow day 0 is the first bar after every Ready rule holds,
and Ready rule 5 asks for the engine version, config hash, pre-registration
hash and data manifest hash to be recorded at day 0. This module checks the
rules a program can check, lists the ones only the Code Owner can confirm,
and builds the day-0 record. It reads and hashes files only through the
files port (issue #1186); the ``hermes shadow-start`` command archives the
ledger and writes the record.

Reference: docs/architecture/adr-021-strategy-lifecycle-ready-and-done.md §2,
§3; openspec/archive/797-shadow-day-zero/change.md.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from hermes.domain.calibration.certification import CertificationRecord, CertificationState
from hermes.ports.files import FilesPort

__all__ = [
    "MANUAL_RULES",
    "Check",
    "DayZeroRecord",
    "Verdict",
    "certification_check",
    "notebook_check",
    "ready",
    "roster_check",
    "sha256_tree",
]


class Verdict(StrEnum):
    """Outcome of one Ready rule."""

    PASS = "pass"  # noqa: S105 — a verdict, not a credential
    FAIL = "fail"
    MANUAL = "confirm by hand"


@dataclass(frozen=True, slots=True)
class Check:
    """One ADR-021 §2 Ready rule as checked.

    Attributes:
        rule: Rule number in ADR-021 §2.
        name: What the rule asks.
        verdict: Pass, fail, or for the Code Owner to confirm.
        detail: Why, without strategy outcomes (ADR-012 D-06).
    """

    rule: int
    name: str
    verdict: Verdict
    detail: str


MANUAL_RULES: tuple[Check, ...] = (
    Check(3, "known decision-changing defects fixed", Verdict.MANUAL, "Code Owner's call"),
    Check(
        4,
        "§10 shadow kills and §7 drift check run inside serve",
        Verdict.MANUAL,
        "confirm the release carries issue #796",
    ),
    Check(
        6,
        "backtest and serve make the same decisions",
        Verdict.MANUAL,
        "confirm `hermes parity check` passed on the latest shadow day",
    ),
)
"""Rules no program here can decide; ``--confirm START`` is the Code Owner's word on them."""

_CERTIFIED_OR_LATER = frozenset(CertificationState) - {CertificationState.UNTRUSTED}


def certification_check(record: CertificationRecord | None, fingerprint: str) -> Check:
    """Rule 2: the backtest gate is recorded for the parameters serve runs.

    Args:
        record: The strategy's certification record, or ``None`` when none exists.
        fingerprint: The parameter fingerprint of the configuration about to run.

    Returns:
        PASS when the record is past ``UNTRUSTED`` with the same fingerprint.
    """
    name = "backtest gate recorded by `hermes certify`"
    if record is None or record.state not in _CERTIFIED_OR_LATER:
        state = "no record" if record is None else record.state.value
        return Check(2, name, Verdict.FAIL, f"certification state is {state}")
    if record.parameter_fingerprint != fingerprint:
        return Check(2, name, Verdict.FAIL, "certified parameters differ from the config")
    return Check(2, name, Verdict.PASS, f"state {record.state.value}, same parameters")


def notebook_check(pre_registration: Path, notebook: Path, *, files: FilesPort) -> Check:
    """Rule 1: the pre-registration is signed and its hash is in the lab notebook.

    Args:
        pre_registration: The pre-registration document.
        notebook: The experiment's lab notebook.
        files: Reads both.

    Returns:
        PASS when the front matter says ``status: SIGNED`` and its
        ``content_sha256`` appears in the notebook.
    """
    name = "signed pre-registration with its hash in the lab notebook"
    front = _front_matter(files.read_text(pre_registration))
    if front.get("status") != "SIGNED":
        return Check(1, name, Verdict.FAIL, "pre-registration is not signed")
    digest = front.get("content_sha256", "")
    if not digest or digest not in files.read_text(notebook):
        return Check(1, name, Verdict.FAIL, "signed hash is not in the lab notebook")
    return Check(1, name, Verdict.PASS, f"signed hash {digest[:12]} in the notebook")


def roster_check(enabled: Sequence[str], strategy_id: str) -> Check:
    """Rule 7: the strategy is the one enabled roster entry, with its own id.

    Args:
        enabled: Names of the enabled roster entries.
        strategy_id: The strategy the run is for.

    Returns:
        PASS when exactly that strategy is enabled.
    """
    name = "own strategy id, virtual capital and ledger rows"
    if list(enabled) != [strategy_id]:
        return Check(7, name, Verdict.FAIL, f"enabled roster is {list(enabled)}")
    return Check(7, name, Verdict.PASS, f"{strategy_id} is the enabled roster entry")


def ready(checks: Iterable[Check]) -> bool:
    """Return whether no checked rule failed (manual rules wait for the Code Owner)."""
    return all(c.verdict is not Verdict.FAIL for c in checks)


def sha256_tree(directory: Path, *, files: FilesPort) -> str:
    """Return one SHA-256 over every file under ``directory``, path and bytes.

    Files are taken in sorted relative-path order, so the hash does not
    depend on the file system's listing order.

    Args:
        directory: The tree to hash.
        files: Lists and hashes its files.
    """
    digest = hashlib.sha256()
    for path in sorted(files.files_under(directory)):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(files.sha256(path).encode("ascii") + b"\n")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DayZeroRecord:
    """What ADR-021 Ready rule 5 asks to record when a counted shadow run starts.

    Attributes:
        run_id: Id of the new shadow run.
        strategy_id: The strategy the run is for.
        day_zero_at: When the run was started (UTC).
        package_version: Installed hermes version.
        release: The live release directory name (the engine build).
        config_sha256: Hash of the config directory.
        preregistration_sha256: Hash of the pre-registration file as deployed.
        preregistration_signed_sha256: The ``content_sha256`` it was signed with.
        data_manifest_sha256: Hash of the data manifest.
        parameter_fingerprint: The parameters serve trades with.
        archived_ledger: Path of the read-only copy of the warm-up ledger.
        archived_ledger_sha256: Hash of that copy.
        checks: Every Ready rule as it stood.
    """

    run_id: str
    strategy_id: str
    day_zero_at: datetime
    package_version: str
    release: str
    config_sha256: str
    preregistration_sha256: str
    preregistration_signed_sha256: str
    data_manifest_sha256: str
    parameter_fingerprint: str
    archived_ledger: str | None
    archived_ledger_sha256: str | None
    checks: tuple[Check, ...]

    def __post_init__(self) -> None:
        """Refuse a naive time or a record without its checks.

        Raises:
            ValueError: ``day_zero_at`` is naive or ``checks`` is empty.
        """
        if self.day_zero_at.tzinfo is None:
            raise ValueError("day_zero_at must be timezone-aware")
        if not self.checks:
            raise ValueError("a day-0 record carries its Ready checks")

    def to_json(self) -> str:
        """Return the record as indented, key-sorted JSON."""
        payload: dict[str, Any] = asdict(self)
        payload["day_zero_at"] = self.day_zero_at.isoformat()
        payload["checks"] = [{**asdict(c), "verdict": c.verdict.value} for c in self.checks]
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _front_matter(text: str) -> dict[str, str]:
    """Return the ``key: value`` lines between the first two ``---`` lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out
