"""``hermes config check``: load the config and run the startup checks, start nothing (#537).

Usage: ``hermes config check [--config-dir DIR] [--json]``

Does what ``hermes serve`` does before it wires anything: loads the config
(with the same ``.env.local`` and ``HERMES_*`` override rules) and runs
:func:`hermes.entrypoints.serve.check_startup`. It makes no network call and
writes nothing. Each problem is named: an unset secret by its environment
variable, an invalid value by its path in the config. A secret's value is
never printed, nor is the value of an invalid field.

Exit codes: 0 clean, 1 findings, 2 usage, 3 config could not be read.

Reference: openspec/changes/537-config-check/change.md.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TextIO

from pydantic import ValidationError

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.root import HermesConfig

__all__ = ["Finding", "main"]

USAGE: Final = "Usage: hermes config check [--config-dir DIR] [--json]"
_EXIT_CODES: Final = "Exit codes: 0 clean, 1 findings, 2 usage, 3 config could not be read."
_CHANNEL_SECRETS: Final = (
    "HERMES_TELEGRAM__BOT_TOKEN",
    "HERMES_TELEGRAM__CHAT_ID",
    "HERMES_SLACK__BOT_TOKEN",
    "HERMES_SLACK__DEFAULT_CHANNEL",
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem the check found.

    Attributes:
        kind: ``missing_secret``, ``invalid_value`` or ``startup``.
        name: The environment variable or config path at fault, never a value.
        detail: What is wrong, without any value.
    """

    kind: str
    name: str
    detail: str


def main(
    argv: list[str],
    *,
    stream: TextIO,
    env: Mapping[str, str] | None = None,
    err: TextIO | None = None,
) -> int:
    """Run ``hermes config check``.

    Args:
        argv: Arguments after ``config check``.
        stream: Output stream.
        env: Environment snapshot; ``None`` reads ``os.environ`` after
            ``.env.local``, as ``hermes serve`` does. A fake in tests.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes config check", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write(f"ERROR: bad arguments. {USAGE}\n")
        return 2
    if parsed.help:
        stream.write(f"{USAGE}\n\nLoads the config and runs the startup checks without starting\n")
        stream.write(f"anything. Secret values are never printed.\n{_EXIT_CODES}\n")
        return 0

    snapshot = None if env is None else dict(env)
    try:
        config = load_config(parsed.config_dir, env=snapshot)
    except ConfigError as exc:
        invalid = _invalid_values(exc)
        if invalid:
            return _report(invalid, parsed.json, stream)
        err.write(f"ERROR: config could not be read: {_without_values(exc)}\n")
        return 3

    findings = _startup_findings(config, dict(os.environ) if snapshot is None else snapshot)
    return _report(findings, parsed.json, stream)


def _invalid_values(exc: ConfigError) -> list[Finding]:
    """Name each field pydantic rejected, from the error's locations alone."""
    cause = exc.__cause__
    if not isinstance(cause, ValidationError):
        return []
    return [
        Finding("invalid_value", _path(err["loc"]), err["type"])
        for err in cause.errors(include_input=False, include_url=False)
    ]


def _path(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in loc) or "config"


def _without_values(exc: ConfigError) -> str:
    """Return the first line of ``exc``: the file and cause, not a document dump."""
    return str(exc).splitlines()[0]


def _startup_findings(config: HermesConfig, env: Mapping[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    try:
        serve_module.check_startup(
            config,
            env,
            certification_store=JsonCertificationStore(
                serve_module.certification_dir_for(serve_module.DEFAULT_DB_PATH)
            ),
        )
    except serve_module.StartupError as exc:
        findings.append(Finding("startup", "startup", str(exc)))
        if config.env == "prod":
            findings.extend(
                Finding("missing_secret", name, "not set or empty")
                for name in _CHANNEL_SECRETS
                if not env.get(name, "").strip()
            )
    return findings


def _report(findings: list[Finding], as_json: bool, stream: TextIO) -> int:
    if as_json:
        stream.write(json.dumps({"ok": not findings, "findings": [asdict(f) for f in findings]}))
        stream.write("\n")
    elif findings:
        for finding in findings:
            stream.write(f"FINDING {finding.kind} {finding.name}: {finding.detail}\n")
    else:
        stream.write("OK: config loads and the startup checks pass.\n")
    return 1 if findings else 0
