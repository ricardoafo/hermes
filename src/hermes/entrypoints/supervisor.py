"""``hermes supervisor``: the read-only, outcome-blind supervisor tools.

Three modes over the same tools and the same deny-by-default grants:

- ``hermes supervisor list`` prints the granted tools;
- ``hermes supervisor call TOOL [--since ISO]`` prints one tool's JSON, for
  routines and the supervisor desk (#365);
- ``hermes supervisor mcp`` is the ``hermes-mcp`` server: MCP over stdio,
  which a client reaches by SSH over the private network (ADR-012 D-07);
- ``hermes supervisor brief [--weekly]`` prints the desk's daily line or
  weekly brief, built by code from the same tools and checked by the output
  gate (#365). When the full brief fails the gate, the digest of states and
  counts is printed instead and the gate's reasons go to the audit stream.

Grants come from ``--grant SCOPE`` (repeatable) or ``HERMES_SUPERVISOR_GRANTS``
(comma-separated). Nothing is granted by default and there is no wildcard.
Every call is audited as one JSON line on stderr, or appended to ``--audit``.

The ledger is opened with SQLite's ``mode=ro`` for each call, so the server
never writes and is safe next to a running ``hermes serve``.

Exit codes: 0 ok, 1 the tool refused the call, 2 usage, 3 config error.

Reference: openspec/changes/34-supervisor-read-tools/change.md; issues #34, #470.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, TextIO

import hermes
from hermes.adapters.certification.json_store import JsonCertificationStore
from hermes.adapters.journal.jsonl_store import JsonlDecisionJournal
from hermes.adapters.ledger.supervisor_reader import SqliteSupervisorReader
from hermes.application.status import UnitState
from hermes.application.supervisor.briefing import BriefKind, gather, publishable
from hermes.application.supervisor.mcp import McpServer
from hermes.application.supervisor.tools import (
    MEMORIZATION_GUARD,
    AuditRecord,
    SupervisorContext,
    ToolDispatcher,
    UnknownToolError,
)
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints import status as status_module
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.root import HermesConfig
from hermes.ports.supervisor import CertificationFact, CertificationLookup

__all__ = ["GRANTS_ENV_VAR", "main"]

GRANTS_ENV_VAR: Final = "HERMES_SUPERVISOR_GRANTS"
_USAGE: Final = (
    "Usage: hermes supervisor list|mcp [OPTIONS]\n"
    "       hermes supervisor call TOOL [--since ISO] [OPTIONS]\n"
    "       hermes supervisor brief [--weekly] [OPTIONS]\n"
    "\n"
    "Read-only, outcome-blind supervisor tools; `mcp` serves them over stdio.\n"
    "Options: --grant SCOPE (repeatable; or HERMES_SUPERVISOR_GRANTS), --config-dir DIR,\n"
    "         --db PATH, --journal FILE, --store-dir DIR, --guard-date YYYY-MM-DD,\n"
    "         --audit FILE\n"
    "Exit codes: 0 ok, 1 refused, 2 usage, 3 config error.\n"
)


def main(
    argv: list[str],
    *,
    stream: TextIO,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    audit_stream: TextIO | None = None,
    now: Callable[[], datetime] | None = None,
    unit_state: Callable[[], UnitState] | None = None,
    err: TextIO | None = None,
) -> int:
    """Handle ``hermes supervisor``.

    Args:
        argv: Residual argv after the ``supervisor`` token.
        stream: Output stream; the protocol stream in ``mcp`` mode.
        env: Environment snapshot for the grants; defaults to empty.
        stdin: Protocol input in ``mcp`` mode; defaults to ``sys.stdin``.
        audit_stream: Where audit lines go without ``--audit``; defaults to
            ``sys.stderr``.
        now: Wall clock; injected by tests.
        unit_state: systemd probe; defaults to ``hermes status``'s.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    try:
        args = _parser().parse_args(argv)
    except SystemExit:
        err.write("ERROR: could not parse `hermes supervisor` arguments.\n" + _USAGE)
        return 2
    if args.help or args.mode is None:
        stream.write(_USAGE)
        return 0 if args.help else 2
    if args.mode == "call" and not args.tool:
        err.write("ERROR: `call` needs a TOOL.\n" + _USAGE)
        return 2
    try:
        # An explicit env snapshot skips the .env.local load: the supervisor never
        # reads a dotenv file, so no secret can reach it that way (ADR-012 D-07).
        config = load_config(args.config_dir, env=dict(env or {}))
    except ConfigError as exc:
        err.write(f"ERROR: config load failed: {exc}\n")
        return 3
    clock = now or (lambda: datetime.now(UTC))
    grants = [*args.grant, *_env_grants(env or {})]
    diagnostics = audit_stream or sys.stderr
    with _audit_sink(args.audit, diagnostics) as audit:
        try:
            dispatcher = ToolDispatcher(
                grants=grants,
                context=_context_factory(args, config, clock, unit_state),
                audit=audit,
                clock=clock,
            )
        except ValueError as exc:
            err.write(f"ERROR: {exc}\n")
            return 2
        return _run_mode(dispatcher, args, stream, stdin, diagnostics=diagnostics, clock=clock)


def _run_mode(
    dispatcher: ToolDispatcher,
    args: argparse.Namespace,
    stream: TextIO,
    stdin: TextIO | None,
    *,
    diagnostics: TextIO,
    clock: Callable[[], datetime],
) -> int:
    if args.mode == "brief":
        return _brief(dispatcher, args, stream, diagnostics, clock)
    if args.mode == "list":
        stream.writelines(
            f"{tool.name:<14} {tool.scope:<28} {tool.summary}\n" for tool in dispatcher.available()
        )
        return 0
    if args.mode == "mcp":
        return _serve(McpServer(dispatcher, version=hermes.__version__), stdin, stream)
    return _call(dispatcher, args, stream)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes supervisor", add_help=False)
    parser.add_argument("mode", nargs="?", choices=("list", "call", "mcp", "brief"))
    parser.add_argument("tool", nargs="?")
    parser.add_argument("--since", default=None)
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--grant", action="append", default=[])
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("--journal", type=Path, default=None)
    parser.add_argument("--store-dir", type=Path, default=None)
    parser.add_argument("--guard-date", type=_guard_date, default=MEMORIZATION_GUARD)
    parser.add_argument("--audit", type=Path, default=None)
    parser.add_argument("-h", "--help", action="store_true")
    return parser


def _guard_date(raw: str) -> datetime:
    guard = datetime.fromisoformat(raw).replace(tzinfo=UTC)
    if guard < MEMORIZATION_GUARD:
        raise argparse.ArgumentTypeError(f"before {MEMORIZATION_GUARD.date().isoformat()}")
    return guard


def _env_grants(env: Mapping[str, str]) -> list[str]:
    return [g.strip() for g in env.get(GRANTS_ENV_VAR, "").split(",") if g.strip()]


@contextmanager
def _audit_sink(path: Path | None, fallback: TextIO) -> Iterator[Callable[[AuditRecord], None]]:
    """Yield a writer of one JSON line per audit record."""
    target = path.open("a", encoding="utf-8") if path is not None else fallback

    def write(record: AuditRecord) -> None:
        line = {
            "at": record.at.isoformat(),
            "tool": record.tool,
            "scope": record.scope,
            "outcome": record.outcome.value,
            "args_sha256": record.args_sha256,
            "output_sha256": record.output_sha256,
        }
        target.write(json.dumps(line, sort_keys=True) + "\n")
        target.flush()

    try:
        yield write
    finally:
        if path is not None:
            target.close()


def _context_factory(
    args: argparse.Namespace,
    config: HermesConfig,
    clock: Callable[[], datetime],
    unit_state: Callable[[], UnitState] | None,
) -> Callable[[], AbstractContextManager[SupervisorContext]]:
    market = config.market_data
    db_path: Path = args.db
    journal = JsonlDecisionJournal(args.journal or db_path.parent / "decision-journal.jsonl")
    store = JsonCertificationStore(args.store_dir or serve_module.certification_dir_for(db_path))
    probe = unit_state or status_module.systemd_unit_state

    @contextmanager
    def open_context() -> Iterator[SupervisorContext]:
        with SqliteSupervisorReader.open(db_path) as reader:
            yield SupervisorContext(
                reader=reader,
                pairs=tuple(market.parsed_pairs()),
                intervals=tuple(market.intervals),
                settle=timedelta(seconds=market.settle_delay_seconds),
                grace=timedelta(seconds=market.stale_after_seconds),
                unit=probe(),
                now=clock(),
                journal=journal,
                certification=_certification_lookup(store),
                strategy_id=serve_module.SHADOW_STRATEGY_NAME,
                guard=args.guard_date,
            )

    return open_context


def _certification_lookup(store: JsonCertificationStore) -> CertificationLookup:
    def lookup(strategy_id: str) -> CertificationFact | None:
        record = store.load(strategy_id)
        if record is None:
            return None
        entered = record.transitions[-1].at if record.transitions else None
        return CertificationFact(state=record.state.value, entered_at=entered)

    return lookup


def _call(dispatcher: ToolDispatcher, args: argparse.Namespace, stream: TextIO) -> int:
    tool_args: dict[str, object] = {} if args.since is None else {"since": args.since}
    try:
        result = dispatcher.call(args.tool, tool_args)
    except UnknownToolError as exc:
        stream.write(f"REFUSED: {exc} (not a tool, or its scope is not granted)\n")
        return 1
    if result.payload is None:
        stream.write(f"REFUSED: {result.error}\n")
        return 1
    stream.write(json.dumps(result.payload, indent=2, sort_keys=True) + "\n")
    return 0


def _brief(
    dispatcher: ToolDispatcher,
    args: argparse.Namespace,
    stream: TextIO,
    diagnostics: TextIO,
    clock: Callable[[], datetime],
) -> int:
    kind = BriefKind.WEEKLY if args.weekly else BriefKind.DAILY
    brief, verdict = publishable(gather(dispatcher, kind=kind, now=clock()), kind=kind)
    if not verdict.passed:
        line = {"brief": kind.value, "gate": "digest", "reasons": list(verdict.reasons)}
        diagnostics.write(json.dumps(line, sort_keys=True) + "\n")
    if not brief.gate().passed:
        stream.write("REFUSED: the brief did not pass the output gate.\n")
        return 1
    stream.write(brief.render())
    return 0


def _serve(server: McpServer, stdin: TextIO | None, stream: TextIO) -> int:
    for line in stdin or sys.stdin:
        reply = server.handle_line(line)
        if reply is not None:
            stream.write(json.dumps(reply, separators=(",", ":")) + "\n")
            stream.flush()
    return 0
