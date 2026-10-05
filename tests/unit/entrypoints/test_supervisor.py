"""Tests for ``hermes supervisor`` end to end over a real ledger file (#34).

Invariants protected here:

- the command is registered and reachable through ``hermes``;
- grants come from ``--grant`` and ``HERMES_SUPERVISOR_GRANTS``; without one
  nothing is listed or callable (exit 1), and an unknown scope is a usage error;
- ``call`` prints a tool's JSON and exits 0, or ``REFUSED`` and exits 1;
- ``mcp`` answers newline-delimited JSON-RPC on stdout, one reply per request;
- every call leaves one audit line, on stderr or in ``--audit``;
- the ledger file is opened read-only and never created.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from hermes.application.status import UnitState
from hermes.domain.strategy.signal import SignalKind
from hermes.entrypoints import supervisor as supervisor_module
from hermes.entrypoints.commands import discover
from tests.support.status_ledger import ETH, LAST_4H, NOW, signal, write_ledger

_CONFIG = Path(__file__).resolve().parents[3] / "config"


def _run(
    args: list[str],
    db: Path,
    *,
    env: dict[str, str] | None = None,
    stdin: str = "",
    err: io.StringIO | None = None,
) -> tuple[int, str, str]:
    out, audit = io.StringIO(), io.StringIO()
    code = supervisor_module.main(
        [*args, "--config-dir", str(_CONFIG), "--db", str(db)],
        stream=out,
        env=env or {},
        stdin=io.StringIO(stdin),
        audit_stream=audit,
        err=err,
        now=lambda: NOW,
        unit_state=lambda: UnitState.RUNNING,
    )
    return code, out.getvalue(), audit.getvalue()


def test_the_command_is_registered() -> None:
    """``hermes supervisor`` is discovered from its own command module."""
    assert "supervisor" in discover()


def test_call_prints_the_tool_json(tmp_path: Path) -> None:
    """A granted call prints the projection and exits 0."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    code, out, _ = _run(["call", "health", "--grant", "hermes:read:health"], db)

    assert code == 0
    assert json.loads(out)["verdict"] == "ok"


def test_grants_can_come_from_the_environment(tmp_path: Path) -> None:
    """HERMES_SUPERVISOR_GRANTS grants scopes like repeated --grant flags."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    env = {"HERMES_SUPERVISOR_GRANTS": "hermes:read:health, hermes:read:positions"}

    code, out, _ = _run(["list"], db, env=env)

    assert code == 0
    assert [line.split()[0] for line in out.splitlines()] == ["health", "positions"]


def test_without_a_grant_a_call_is_refused(tmp_path: Path) -> None:
    """Deny-by-default: no grant means REFUSED and exit 1, with a DENIED audit line."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    code, out, audit = _run(["call", "health"], db)

    assert code == 1
    assert out.startswith("REFUSED:")
    assert json.loads(audit)["outcome"] == "denied"


def test_an_unknown_scope_is_a_usage_error(tmp_path: Path) -> None:
    """A wildcard scope is rejected with exit 2, never treated as a broad grant."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    err = io.StringIO()
    code, out, _ = _run(["list", "--grant", "hermes:read:*"], db, err=err)

    assert (code, out) == (2, "")
    assert "unknown scope" in err.getvalue()


def test_signals_before_the_guard_are_refused(tmp_path: Path) -> None:
    """``--since`` before 2026-02-01 exits 1 with the guard reason."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    code, out, _ = _run(
        [
            "call",
            "signals",
            "--since",
            "2025-12-01T00:00:00+00:00",
            "--grant",
            "hermes:read:signals",
        ],
        db,
    )

    assert code == 1
    assert "memorization guard" in out


def test_signals_are_listed_without_prices(tmp_path: Path) -> None:
    """A recorded signal comes back as pair, kind and time; its price is absent."""
    db = write_ledger(
        tmp_path / "hermes.sqlite3", signals=(signal(ETH, SignalKind.ENTRY, LAST_4H, "2618.45"),)
    )

    code, out, _ = _run(
        [
            "call",
            "signals",
            "--since",
            "2026-10-01T00:00:00+00:00",
            "--grant",
            "hermes:read:signals",
        ],
        db,
    )

    assert code == 0
    assert json.loads(out)["signals"] == [
        {"pair": str(ETH), "kind": "entry", "occurred_at": LAST_4H.isoformat()}
    ]
    assert "2618" not in out


def test_a_missing_ledger_is_refused_and_not_created(tmp_path: Path) -> None:
    """A wrong --db path refuses the call and leaves no empty file behind."""
    db = tmp_path / "absent.sqlite3"

    code, out, _ = _run(["call", "health", "--grant", "hermes:read:health"], db)

    assert (code, out.strip()) == (1, "REFUSED: FileNotFoundError")
    assert not db.exists()


def test_mcp_mode_answers_each_request_on_its_own_line(tmp_path: Path) -> None:
    """The stdio server replies once per request and stays silent on notifications."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "health"}},
    ]
    stdin = "".join(json.dumps(line) + "\n" for line in lines)

    code, out, audit = _run(["mcp", "--grant", "hermes:read:health"], db, stdin=stdin)

    replies = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert [t["name"] for t in replies[1]["result"]["tools"]] == ["health"]
    assert replies[2]["result"]["structuredContent"]["verdict"] == "ok"
    assert len(audit.splitlines()) == 1


def test_audit_lines_are_appended_to_the_audit_file(tmp_path: Path) -> None:
    """With --audit, each call appends one JSON line to that file."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    audit_file = tmp_path / "audit.jsonl"
    args = ["call", "health", "--grant", "hermes:read:health", "--audit", str(audit_file)]

    _run(args, db)
    _run(args, db)

    records = [json.loads(line) for line in audit_file.read_text().splitlines()]
    assert [(r["tool"], r["outcome"]) for r in records] == [("health", "ok"), ("health", "ok")]


def test_a_guard_date_before_the_floor_is_a_usage_error(tmp_path: Path) -> None:
    """--guard-date cannot lower the memorization guard below 2026-02-01."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    code, _, _ = _run(["list", "--guard-date", "2025-06-01"], db)

    assert code == 2


def test_no_mode_is_a_usage_error_and_help_is_not(tmp_path: Path) -> None:
    """Bare ``hermes supervisor`` exits 2 with usage; ``--help`` exits 0."""
    db = tmp_path / "hermes.sqlite3"

    assert _run([], db)[0] == 2
    assert _run(["--help"], db)[0] == 0
    assert _run(["call"], db)[0] == 2


def test_a_broken_config_dir_exits_3(tmp_path: Path) -> None:
    """An unreadable config is exit 3, before any tool runs."""
    out = io.StringIO()
    code = supervisor_module.main(
        ["list", "--config-dir", str(tmp_path / "nope")], stream=out, env={}
    )

    assert code == 3


_ALL_GRANTS = {
    "HERMES_SUPERVISOR_GRANTS": ",".join(
        f"hermes:read:{n}"
        for n in ("health", "signals", "engine", "positions", "decisions", "certification")
    )
}


def test_brief_prints_the_daily_line_from_a_real_ledger(tmp_path: Path) -> None:
    """``brief`` prints the cited daily line and the engine, auditing one read per tool (#365)."""
    db = write_ledger(
        tmp_path / "hermes.sqlite3", signals=(signal(ETH, SignalKind.ENTRY, LAST_4H, "2618.45"),)
    )

    code, out, audit = _run(["brief"], db, env=_ALL_GRANTS)

    lines = out.splitlines()
    assert code == 0
    assert lines[0] == "Hermes daily brief, 2026-10-03 16:10 UTC"
    assert "1 signal(s), 1 open position(s)" in lines[1]
    assert lines[1].endswith("[health] [signals] [positions] [decisions]")
    assert lines[2].startswith("- What the engine did: BTC/USDT: 1 4h and 1 1d bars checked;")
    assert "ETH/USDT: 1 4h and 1 1d bars checked; 1 entry signal(s); no fills; holding" in out
    assert "2618" not in out
    assert len(audit.splitlines()) == 6


def test_brief_weekly_lists_every_item(tmp_path: Path) -> None:
    """``brief --weekly`` prints one line per item after the title."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    code, out, _ = _run(["brief", "--weekly"], db, env=_ALL_GRANTS)

    headings = [line.split(":")[0] for line in out.splitlines()[1:]]
    assert code == 0
    assert headings == [
        "- Health",
        "- Signals",
        "- What the engine did",
        "- Open positions",
        "- Decisions waiting",
        "- Certification",
        "- Fill drift",
        "- Kill triggers",
        "- Model spend",
    ]


def test_brief_without_grants_reads_nothing(tmp_path: Path) -> None:
    """Deny-by-default holds for the brief: every read is refused and audited."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    code, out, audit = _run(["brief"], db)

    assert code == 0
    assert "Health unavailable." in out
    assert {json.loads(line)["outcome"] for line in audit.splitlines()} == {"denied"}
