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
from datetime import timedelta
from pathlib import Path

import pytest

from hermes.application.console import ViewNotAvailableError
from hermes.application.status import UnitState
from hermes.domain.strategy.signal import SignalKind
from hermes.entrypoints import supervisor as supervisor_module
from hermes.entrypoints.commands import discover
from tests.support.status_ledger import ETH, LAST_4H, NOW, fill, signal, write_ledger

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
    entry = signal(ETH, SignalKind.ENTRY, LAST_4H, "2618.45")
    db = write_ledger(tmp_path / "hermes.sqlite3", signals=(entry,), fills=(fill(entry),))

    code, out, audit = _run(["brief"], db, env=_ALL_GRANTS)

    lines = out.splitlines()
    assert code == 0
    assert lines[0] == "Hermes daily brief, 2026-10-03 16:10 UTC"
    assert "1 signal(s), 1 open position(s)" in lines[1]
    assert lines[1].endswith("[health] [signals] [positions] [decisions]")
    assert lines[2].startswith("- What the engine did: BTC/USDT: 1 4h and 1 1d bars checked;")
    assert (
        "ETH/USDT: 1 4h and 1 1d bars checked; 1 entry signal(s); fills: 1 buy, 0 sell; holding"
        in out
    )
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


def test_desk_briefs_serve_the_published_daily_brief(tmp_path: Path) -> None:
    """The console's desk source holds the same daily brief ``brief`` prints (#720)."""
    db = write_ledger(
        tmp_path / "hermes.sqlite3", signals=(signal(ETH, SignalKind.ENTRY, LAST_4H, "2618.45"),)
    )
    source = supervisor_module.desk_briefs(
        db, config_dir=_CONFIG, clock=lambda: NOW, unit_state=lambda: UnitState.RUNNING
    )

    (brief,) = source()

    _, printed, _ = _run(["brief"], db, env=_ALL_GRANTS)
    shown = [
        f"- {s.heading}: {s.text} {' '.join(f'[{c}]' for c in s.sources)}" for s in brief.sections
    ]
    assert (brief.kind, brief.as_of, brief.digest) == ("daily", NOW, False)
    assert shown == printed.splitlines()[1:]
    assert "2618" not in "\n".join(shown)


def test_desk_briefs_are_built_once_per_cache_window(tmp_path: Path) -> None:
    """Within :data:`BRIEF_CACHE` the console reuses the brief; after it, builds a new one."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    now = [NOW]
    source = supervisor_module.desk_briefs(
        db, config_dir=_CONFIG, clock=lambda: now[0], unit_state=lambda: UnitState.RUNNING
    )

    first = source()
    now[0] = NOW + supervisor_module.BRIEF_CACHE - timedelta(seconds=1)
    cached = source()
    now[0] = NOW + supervisor_module.BRIEF_CACHE
    rebuilt = source()

    assert cached is first
    assert rebuilt[0].as_of == NOW + supervisor_module.BRIEF_CACHE


def test_desk_briefs_without_a_config_are_not_available(tmp_path: Path) -> None:
    """A config that cannot load makes the Briefs view "not available", not a crash."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    source = supervisor_module.desk_briefs(
        db, config_dir=tmp_path / "nope", clock=lambda: NOW, unit_state=lambda: UnitState.RUNNING
    )

    with pytest.raises(ViewNotAvailableError, match="config load failed"):
        source()


def _kept(db: Path) -> list[dict[str, object]]:
    path = db.parent / supervisor_module.BRIEF_ARCHIVE_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_brief_keep_stores_exactly_what_was_printed_beside_the_ledger(tmp_path: Path) -> None:
    """Invariant: the kept brief is the text that passed the output gate, with no price in it."""
    db = write_ledger(
        tmp_path / "hermes.sqlite3", signals=(signal(ETH, SignalKind.ENTRY, LAST_4H, "2618.45"),)
    )

    code, printed, _ = _run(["brief", "--keep"], db, env=_ALL_GRANTS)

    (line,) = _kept(db)
    sections = line["sections"]
    assert isinstance(sections, list)
    shown = [
        f"- {s['heading']}: {s['text']} {' '.join(f'[{c}]' for c in s['sources'])}"
        for s in sections
    ]
    assert (code, line["kind"], line["as_of"], line["digest"]) == (
        0,
        "daily",
        NOW.isoformat(),
        False,
    )
    assert shown == printed.splitlines()[1:]
    assert "2618" not in json.dumps(line)


def test_brief_without_keep_writes_nothing(tmp_path: Path) -> None:
    """Invariant: only the timer's explicit ``--keep`` run writes next to the ledger."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    _run(["brief"], db, env=_ALL_GRANTS)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["hermes.sqlite3"]


def test_brief_keep_twice_the_same_day_keeps_one_brief(tmp_path: Path) -> None:
    """Invariant: a manual run beside the timer does not list the day twice."""
    db = write_ledger(tmp_path / "hermes.sqlite3")

    codes = [_run(["brief", "--keep"], db, env=_ALL_GRANTS)[0] for _ in range(2)]

    assert (codes, len(_kept(db))) == ([0, 0], 1)


def test_brief_keep_into_a_named_file(tmp_path: Path) -> None:
    """Invariant: ``--briefs FILE`` moves the archive, and the ledger's directory stays clean."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    target = tmp_path / "elsewhere" / "kept.jsonl"

    code, _, _ = _run(["brief", "--keep", "--briefs", str(target)], db, env=_ALL_GRANTS)

    assert (code, target.is_file(), (tmp_path / "briefs.jsonl").exists()) == (0, True, False)


def test_a_brief_that_cannot_be_kept_exits_1_and_says_so(tmp_path: Path) -> None:
    """Invariant: the timer unit fails, so ``systemctl --failed`` shows it, when nothing is kept."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    (tmp_path / "briefs.jsonl").mkdir()
    out, audit, err = io.StringIO(), io.StringIO(), io.StringIO()

    code = supervisor_module.main(
        ["brief", "--keep", "--config-dir", str(_CONFIG), "--db", str(db)],
        stream=out,
        env=_ALL_GRANTS,
        audit_stream=audit,
        err=err,
        now=lambda: NOW,
        unit_state=lambda: UnitState.RUNNING,
    )

    assert code == 1
    assert "the brief was not kept" in audit.getvalue()


def test_desk_briefs_list_the_kept_days_behind_today(tmp_path: Path) -> None:
    """Invariant: the Briefs screen lists earlier days, newest first, as they were published."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    for days in (3, 1):
        past = NOW - timedelta(days=days)
        supervisor_module.main(
            ["brief", "--keep", "--config-dir", str(_CONFIG), "--db", str(db)],
            stream=io.StringIO(),
            env=_ALL_GRANTS,
            audit_stream=io.StringIO(),
            now=lambda past=past: past,
            unit_state=lambda: UnitState.RUNNING,
        )
    source = supervisor_module.desk_briefs(
        db, config_dir=_CONFIG, clock=lambda: NOW, unit_state=lambda: UnitState.RUNNING
    )

    briefs = source()

    assert [b.as_of for b in briefs] == [NOW, NOW - timedelta(days=1), NOW - timedelta(days=3)]


def test_todays_kept_brief_gives_way_to_the_live_one(tmp_path: Path) -> None:
    """Invariant: the list shows today once, built live, not the morning copy beside it."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    _run(["brief", "--keep"], db, env=_ALL_GRANTS)
    source = supervisor_module.desk_briefs(
        db,
        config_dir=_CONFIG,
        clock=lambda: NOW + timedelta(hours=2),
        unit_state=lambda: UnitState.RUNNING,
    )

    (today,) = source()

    assert today.as_of == NOW + timedelta(hours=2)


def test_desk_briefs_with_an_unreadable_archive_are_not_available(tmp_path: Path) -> None:
    """Invariant: a corrupt archive is named on the screen, not hidden behind today's brief."""
    db = write_ledger(tmp_path / "hermes.sqlite3")
    (tmp_path / supervisor_module.BRIEF_ARCHIVE_NAME).write_text("[]\n", encoding="utf-8")
    source = supervisor_module.desk_briefs(
        db, config_dir=_CONFIG, clock=lambda: NOW, unit_state=lambda: UnitState.RUNNING
    )

    with pytest.raises(ViewNotAvailableError, match="kept briefs unreadable"):
        source()
