"""``hermes config check``: load the config and run the startup checks, start nothing (#537).

Usage: ``hermes config check [--config-dir DIR] [--env-file PATH|-] [--json]``

Does what ``hermes serve`` does before it wires anything: loads the config
(with the same ``.env.local`` and ``HERMES_*`` override rules) and runs
:func:`hermes.entrypoints.serve.check_startup`. It makes no network call and
writes nothing. Each problem is named: an unset secret by its environment
variable, an invalid value by its path in the config. A secret's value is
never printed, nor is the value of an invalid field. A setting that breaks
its tier's rules (ADR-024 §4) is a ``tier`` finding, named the same way.

``--env-file`` checks an env file instead of the process environment: the
environment is then that file alone, as systemd gives it to a unit with an
``EnvironmentFile=`` and no ``Environment=`` line. ``-`` reads it from stdin,
so the release installer can check a root-only env file as the service user
without the user ever opening it (#970).

Exit codes: 0 clean, 1 findings, 2 usage, 3 config could not be read.

Reference: openspec/archive/537-config-check/change.md.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TextIO

from dotenv import dotenv_values
from pydantic import ValidationError

from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError, TierError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.root import HermesConfig

__all__ = ["Finding", "main"]

USAGE: Final = "Usage: hermes config check [--config-dir DIR] [--env-file PATH|-] [--json]"
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
        kind: ``missing_secret``, ``invalid_value``, ``tier`` or ``startup``.
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
    stdin: TextIO | None = None,
) -> int:
    """Run ``hermes config check``.

    Args:
        argv: Arguments after ``config check``.
        stream: Output stream.
        env: Environment snapshot; ``None`` reads ``os.environ`` after
            ``.env.local``, as ``hermes serve`` does. A fake in tests.
            ``--env-file`` replaces it.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        stdin: Where ``--env-file -`` reads from; defaults to ``sys.stdin``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parser = argparse.ArgumentParser(prog="hermes config check", add_help=False)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--env-file")
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
    if parsed.env_file is not None:
        try:
            snapshot = _read_env_file(parsed.env_file, sys.stdin if stdin is None else stdin)
        except (OSError, UnicodeDecodeError) as exc:
            reason = exc.strerror if isinstance(exc, OSError) else "not UTF-8"
            err.write(f"ERROR: env file could not be read: {reason}\n")
            return 3
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


def _read_env_file(path: str, stdin: TextIO) -> dict[str, str]:
    """Parse an env file without expanding ``${VAR}``, as systemd reads it.

    A key with no ``=`` has no value and is left out.
    """
    if path == "-":
        values = dotenv_values(stream=stdin, interpolate=False)
    else:
        with Path(path).open(encoding="utf-8") as handle:
            values = dotenv_values(stream=handle, interpolate=False)
    return {key: value for key, value in values.items() if value is not None}


def _tier_finding(problem: str) -> Finding:
    """Split a tier problem into the setting it names and what is wrong."""
    name, _, detail = problem.partition(" ")
    return Finding("tier", name, detail)


def _invalid_values(exc: ConfigError) -> list[Finding]:
    """Name each setting at fault: a tier problem, or a field pydantic rejected."""
    if isinstance(exc, TierError):
        return [_tier_finding(problem) for problem in exc.problems]
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
        stream.writelines(
            f"FINDING {finding.kind} {finding.name}: {finding.detail}\n" for finding in findings
        )
    else:
        stream.write("OK: config loads and the startup checks pass.\n")
    return 1 if findings else 0
