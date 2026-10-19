# Change: phase-1-observability-rollback-slack-scaffolds

**SDD change**: `phase-1-observability-rollback-slack-scaffolds`
**Date**: 2026-08-23
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#91)

Mirror of engram topic key `sdd/phase-1-observability-rollback-slack-scaffolds/change` (see [ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention) dual-source rule).

## Summary

Bundled Phase-1-shaping scaffold: structlog observability config, VPS deploy layout that enables symlink-swap rollback, and Slack channel addition alongside Telegram. Each item establishes a pattern that outlives the change; scaffolding the STRUCTURAL parts now avoids painting later Phase-1 code into a corner. Field-name conventions, rollback trigger logic, and adapter implementations fill in during Phase 1 domain work as real usage emerges. Splitting into 3 PRs would triple process overhead for the same aggregate scope.

## Scope

### In

- **Observability**: `src/hermes/infrastructure/__init__.py` + `src/hermes/infrastructure/logging.py` (structlog config, ULID correlation-ID contextvar binding, JSON processor for prod, ConsoleRenderer for dev). One property test in `tests/unit/test_logging.py`. Starter field taxonomy in module docstring.
- **Rollback layout**: `deploy/bootstrap-vps.sh` updated to create `/opt/hermes/{versioned dirs, current symlink, previous symlink}` topology. `.github/workflows/deploy.yml build` job updated to install into `/opt/hermes/{version}/` + swap `current` symlink last. Placeholder bodies still — real rollback fires in Phase 1.
- **Slack channel**: `docs/decisions/2026-08-23-slack-alongside-telegram.md` recording addition + routing table (Telegram = urgent/terse, Slack = rich/reports). `src/hermes/ports/notifier.py` — `NotifierPort` Protocol stub for both adapter kinds. No adapter implementations yet.

### Out

- **Log field additions beyond the starter set** — land alongside first domain code (Money → `amount`+`currency`+`unit`; FIFO → `lot_id`+`consumed_qty`+`remaining_qty`; etc.). Refined per-feature not upfront.
- **Rollback trigger logic in deploy.yml** — needs real services + `hermes engine pause` CLI to exist. Fills in during Phase 1.
- **Slack + Telegram adapter implementations** (`slack_adapter.py`, `telegram_adapter.py`) — land alongside first Phase-1 code that emits notifications.
- **New ADR** for Slack addition — using decisions-journal per ADR-009 §2 (tool addition). Promote to new ADR only if scope expands.

## Decisions

### D-1: Split observability into scaffold-now / fields-later

- **Decision**: Ship structlog config + correlation-ID plumbing NOW with a minimal starter field set. Field-name additions land alongside code that emits them.
- **Alternatives**: (a) Full field taxonomy upfront based on ADRs; (b) skip observability until first Phase-1 code needs to log.
- **Rationale**: Full taxonomy upfront guesses at what matters without concrete use cases (over-specification). Skipping until first-log-needed leaves early code with `print()` debt or missing logs. Scaffold + refine matches the `coverage-check.py --bootstrap` pattern that worked in Phase 0.
- **Cost of reversal**: LOW — adding/renaming fields is a per-callsite change.

### D-2: Deploy layout uses symlink swap for rollback

- **Decision**: `/opt/hermes/current -> /opt/hermes/{version}/` symlink pattern. `deploy.yml build` step installs new version to `/opt/hermes/{new-version}/`, then atomically swaps `current` symlink last. `previous -> /opt/hermes/{old-version}/` maintained for rollback.
- **Alternatives**: (a) Previous-wheel install — keep `previous.whl`, on fail `uv pip install --force-reinstall`; (b) Git-revert workflow triggers new deploy; (c) In-place `uv pip install --force-reinstall` with no rollback path.
- **Rationale**: Symlink swap is atomic (~ms), zero-downtime, industry-standard pattern (matches Debian `update-alternatives`, Capistrano releases, Kubernetes label swaps). Previous-wheel install works but requires re-running `uv pip install` at rollback time (~5s + risk if pip fails). Git-revert wastes a full CD cycle (~2 min). In-place install has no rollback path — rejected on correctness grounds.
- **Cost of reversal**: MEDIUM — changing deploy topology requires VPS re-provisioning.

### D-3: Slack + Telegram both, routed by content class

- **Decision**: Both channels active. Router in `src/hermes/application/notification_router.py` (lands Phase 1) decides which adapter(s) for each event class. Routing table documented in the decisions-journal entry.
- **Alternatives**: (a) Telegram only (status quo per ADR-008); (b) Slack only (rejected — no phone push, worse for incidents); (c) Auto-fallback if primary channel down.
- **Rationale**: User's insight — each tool has a natural lane. Telegram wins for incidents (push notification, seconds). Slack wins for reports (formatting, threads, attachments, cross-references). Splitting by content class means each event uses the right channel for its shape. Auto-fallback adds complexity for a rare failure mode; defer.
- **Cost of reversal**: LOW — dropping one adapter is a config change.

### D-4: NotifierPort Protocol shape

- **Decision**: Single `NotifierPort` Protocol with methods `notify_urgent(event: str, correlation_id: str, message: str) -> None` and `notify_report(event: str, correlation_id: str, title: str, body: str, attachments: list[Path] | None = None) -> None`. Adapters implement whichever they meaningfully support (Telegram implements urgent, no-op on report; Slack implements both).
- **Alternatives**: (a) Two separate ports (UrgentNotifierPort + ReportNotifierPort); (b) Single generic `notify()` method with typed enum.
- **Rationale**: Two methods per port keeps the router simple (call the right method, adapters no-op what they don't support) without splitting the port hierarchy. Single generic method with enum requires router to know adapter capabilities — leaky.
- **Cost of reversal**: MEDIUM — port changes cascade through adapters + router + application layer.

### D-5: Log field starter set

- **Decision**: Every log line has: `timestamp` (ISO 8601 UTC + Z), `level` (lowercase per structlog default), `event` (dotted namespace string, not free-text), `correlation_id` (full ULID), `component` (module path). Others added by callers via `structlog.contextvars.bind_contextvars()` per feature.
- **Alternatives**: (a) Include `service`/`hostname`/`pid` upfront; (b) Free-text `message` field instead of `event`; (c) Numeric levels instead of lowercase strings.
- **Rationale**: Dotted-namespace `event` is queryable (`journalctl | jq 'select(.event == "rebalance.cycle.decided")'`). Free-text messages defeat structured logging. Service/hostname/pid added by systemd/journald automatically — don't duplicate.
- **Cost of reversal**: LOW — rename via ripgrep, wrap in `bind_contextvars` alias if renaming globally.

## Requirements

### R-1: Structlog configured for two environments

- **A-1.1**: `src/hermes/infrastructure/logging.py` exports `configure_logging(*, env: str = "prod") -> None`
- **A-1.2**: `env="prod"` uses `structlog.processors.JSONRenderer` (journald-friendly)
- **A-1.3**: `env="dev"` uses `structlog.dev.ConsoleRenderer` (human-readable colors)
- **A-1.4**: Both include `TimeStamper(fmt="iso", utc=True)`, `add_log_level`, `contextvars.merge_contextvars`
- **A-1.5**: `python -c "from hermes.infrastructure.logging import configure_logging; configure_logging(); import structlog; structlog.get_logger().info('hello', event='test')"` prints valid JSON with `timestamp`/`level`/`event`/`logger` keys

### R-2: Correlation-ID contextvar plumbing

- **A-2.1**: `src/hermes/infrastructure/logging.py` exports `bind_correlation_id(correlation_id: str) -> None` and `new_correlation_id() -> str`
- **A-2.2**: `new_correlation_id()` returns a ULID (26-char Crockford base32)
- **A-2.3**: `bind_correlation_id()` sets a contextvar visible to all subsequent log calls in the same async task
- **A-2.4**: Property test in `tests/unit/test_logging.py` proves correlation-ID stickiness across nested calls via `structlog.testing.capture_logs`

### R-3: Deploy layout supports symlink swap

- **A-3.1**: `deploy/bootstrap-vps.sh` creates `/opt/hermes/` + subdirs `versions/` (holds `v0.1.0/`, `v0.1.1/`, ...), `current` symlink → `versions/<active>`, `previous` symlink → `versions/<prior>`
- **A-3.2**: `bootstrap-vps.sh` is idempotent — re-running preserves symlink targets if they already exist
- **A-3.3**: `.github/workflows/deploy.yml build` job body (placeholder still, per D-2) documents the swap sequence: install to `/opt/hermes/versions/{version}/`, `ln -sfn ... previous`, `ln -sfn ... current`, `systemctl restart hermes-*` (Phase 1 fills in real bodies)

### R-4: Slack + Telegram routing documented

- **A-4.1**: `docs/decisions/2026-08-23-slack-alongside-telegram.md` exists with title, date, related-ADR-008 citation, decision statement, routing table (event class × channel), context, alternatives considered
- **A-4.2**: Routing table covers: service crash, health-check fail, rollback fired, CVE detected, deploy done, weekly rebalance report, monthly tax report, trade fired
- **A-4.3**: Entry added to `docs/decisions/README.md` living index

### R-5: NotifierPort Protocol stub

- **A-5.1**: `src/hermes/ports/notifier.py` exports `NotifierPort` Protocol with `notify_urgent()` and `notify_report()` method signatures
- **A-5.2**: Type hints on both methods — mypy --strict passes
- **A-5.3**: Module docstring cites the decisions-journal entry from R-4

### R-6: pr-preflight + CI green

- **A-6.1**: `bash scripts/dev/pr-preflight.sh` → 9/9 checks pass locally
- **A-6.2**: All required CI checks green on the PR
- **A-6.3**: `import-linter` still reports 3 contracts kept (new file in `infrastructure/` doesn't violate hexagonal boundaries)
- **A-6.4**: `mypy --strict src/hermes` — 0 issues on the 2 new files (`logging.py`, `ports/notifier.py`)

## Design

### `src/hermes/infrastructure/logging.py`

```python
"""Structured logging config.

Wires structlog for hermes: JSON output to stdout (journald captures) in
prod, ConsoleRenderer in dev. Every log line carries a correlation_id
bound via contextvars — set once per rebalance cycle / user command /
adapter call, propagates through every nested call in the same task.

## Field starter set (D-5)

Every log line has:

- `timestamp` — ISO 8601 UTC with Z suffix
- `level` — lowercase (`info`, `warning`, `error`, ...)
- `event` — dotted namespace string (e.g. `rebalance.cycle.decided`);
  NOT free-text — grepping journald depends on this
- `correlation_id` — full ULID (26 chars, Crockford base32); last 6
  chars go into Telegram tails per ADR-008 §Correlation ID convention
- `component` — module path (`hermes.application.rebalance`); source
  of the log call

Additional fields per feature via `bind_contextvars()`. Rules for
adding fields:

- Money values as strings (Decimal-serialized), not floats
- Never log PII (LOPDGDD — see ADR-008 §What NEVER gets logged)
- Prefer dotted-namespace event names over free-text messages

## Usage

    from hermes.infrastructure.logging import (
        configure_logging, bind_correlation_id, new_correlation_id,
    )
    import structlog

    configure_logging(env="prod")  # or "dev" for local
    log = structlog.get_logger(__name__)

    bind_correlation_id(new_correlation_id())
    log.info("rebalance.cycle.started", pair="BTCUSDT")
"""

from __future__ import annotations

import structlog
import ulid
from structlog.contextvars import bind_contextvars, merge_contextvars


def configure_logging(*, env: str = "prod") -> None:
    """Configure structlog processors for the given environment.

    env="prod" → JSON to stdout (journald-friendly, ADR-008 §1)
    env="dev"  → ConsoleRenderer with colors (human-readable local)
    """
    common_processors = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = (
        structlog.processors.JSONRenderer()
        if env == "prod"
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[*common_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        cache_logger_on_first_use=True,
    )


def new_correlation_id() -> str:
    """Generate a fresh ULID (26-char Crockford base32)."""
    return str(ulid.new())


def bind_correlation_id(correlation_id: str) -> None:
    """Bind a correlation ID to the current contextvar scope.

    All subsequent log calls in the same async task will include
    `correlation_id=<value>` automatically via merge_contextvars.
    """
    bind_contextvars(correlation_id=correlation_id)
```

### `tests/unit/test_logging.py`

```python
"""sev1-adjacent: correlation IDs are the primary audit trail per
ADR-008 §1. If binding is broken, post-mortem grep-ability is dead.
"""

from __future__ import annotations

import structlog
from hermes.infrastructure.logging import (
    bind_correlation_id,
    configure_logging,
    new_correlation_id,
)


def test_correlation_id_propagates_across_nested_calls() -> None:
    """A correlation ID bound at the top of a call chain is visible in
    every log emitted deeper in the chain."""
    configure_logging(env="prod")
    cid = new_correlation_id()
    bind_correlation_id(cid)

    with structlog.testing.capture_logs() as captured:
        _inner_call()

    assert len(captured) >= 1
    for entry in captured:
        assert entry.get("correlation_id") == cid


def _inner_call() -> None:
    """Nested function emits a log — should inherit correlation_id."""
    log = structlog.get_logger("hermes.tests.inner")
    log.info("test.event", detail="nested")


def test_new_correlation_id_is_26_char_ulid() -> None:
    cid = new_correlation_id()
    assert len(cid) == 26
    # Crockford base32 alphabet — no I, L, O, U
    forbidden = set("ILOU")
    assert not any(c in forbidden for c in cid)
```

### `src/hermes/ports/notifier.py`

```python
"""Notifier port — Protocol for outbound user-facing notifications.

Two channels per D-3 (see docs/decisions/2026-08-23-slack-alongside-
telegram.md): Telegram for urgent/terse, Slack for rich/reports.
Router at hermes.application.notification_router decides per event
class which adapter(s) fire.

Adapters implement whichever methods they meaningfully support:
- TelegramAdapter: notify_urgent (real), notify_report (no-op)
- SlackAdapter: notify_urgent (real), notify_report (real, rich)
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class NotifierPort(Protocol):
    """Every notifier adapter implements this shape."""

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        """Push-to-phone alert. Short, actionable, includes correlation-ID tail."""
        ...

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        """Rich long-form report. May include formatting, threads, attachments."""
        ...
```

### `deploy/bootstrap-vps.sh` — layout section (added)

```bash
# ─────────────────────────────────────────────────────────────
# Hermes install layout — enables symlink-swap rollback (D-2)
# ─────────────────────────────────────────────────────────────
#
# /opt/hermes/
#   versions/
#     v0.1.0/         <- installed by deploy.yml build step
#       .venv/
#       hermes-0.1.0-py3-none-any.whl
#     v0.1.1/         <- next version installed here
#       .venv/
#       hermes-0.1.1-py3-none-any.whl
#   current -> versions/v0.1.1   <- ACTIVE; systemd units point here
#   previous -> versions/v0.1.0  <- rollback target
#
# Rollback (fills in Phase 1):
#   ln -sfn /opt/hermes/previous /opt/hermes/current
#   systemctl restart hermes-*.service
#
# Idempotent — reruns preserve symlink targets if already present.

install -d -m 0755 -o hermes -g hermes /opt/hermes /opt/hermes/versions

# Ensure symlinks exist (point at nothing initially; deploy.yml populates)
[[ -L /opt/hermes/current ]] || sudo -u hermes ln -s /dev/null /opt/hermes/current
[[ -L /opt/hermes/previous ]] || sudo -u hermes ln -s /dev/null /opt/hermes/previous
```

### `docs/decisions/2026-08-23-slack-alongside-telegram.md`

Full text below in the Design section; ~200 words per ADR-009 §2 format.
Cites ADR-008 §4 as partially superseded. Routing table + reasoning.

### CI workflow updates

`.github/workflows/deploy.yml build` job body — comment updated to
document the symlink-swap sequence (placeholder still — real body
fires in Phase 1 when `hermes engine pause` CLI exists).

## Test plan

Done when:

1. All 6 requirements above pass (A-1.1 through A-6.4)
2. `bash scripts/dev/pr-preflight.sh --render-body phase-1-observability-rollback-slack-scaffolds` → 10 checks pass (9 standard + PR body lint)
3. Every required CI check green on the PR
4. `python -c "from hermes.infrastructure.logging import configure_logging; ..."` produces valid JSON on stdout
5. `NotifierPort` importable and satisfies `isinstance(TestAdapter(), NotifierPort)` (deferred to Phase 1 test — Protocol stub alone doesn't need runtime assertion here)

## Risks

- **`ulid-py` behavior on Python 3.14** — dep is in `pyproject.toml` already but hasn't been exercised. First real import happens in this PR. Mitigation: property test explicitly generates + validates ULID shape.
- **`structlog.testing.capture_logs()` may need JSONRenderer to work** — some versions strip fields at renderer level before capture. Mitigation: property test validates on captured raw entries (before renderer), which is what capture_logs returns.
- **Symlink swap on Windows** — bootstrap-vps.sh runs on Linux VPS only; no Windows concern. But `deploy/README.md` should note this.
- **Symlink `ln -s /dev/null`** as initial state — some systems refuse. Mitigation: use `ln -s versions/.placeholder` and pre-create the placeholder file instead.
- **Overlap with Phase 0** — this bundle closes Phase 0 items (#18 observability, part of #71 meta) but structurally is Phase-1-shaping. Ships during Phase 0 completion; conceptually Phase 1 foundation.

## Dependencies on other changes

- **ulid-py** already in `pyproject.toml` runtime deps (added PR #72). No dep changes needed.
- **structlog** already in `pyproject.toml` runtime deps. No dep changes needed.
- **`.github/workflows/deploy.yml`** exists as skeleton (PR #65). This change updates the `build` job's inline comment; bodies fire in Phase 1.
- **No cross-PR dependencies** — self-contained.
