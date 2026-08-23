"""Health-check registry + integration probes for ``hermes health``.

Each registered integration exposes a synchronous probe callable that
returns a :class:`HealthCheckResult`. The CLI (``entrypoints/cli.py``)
maps subcommand names (e.g. ``telegram``, ``slack``) to registry
entries and prints a status table.

Design notes
------------

- **Registry pattern (scaffold)**: PR E will consolidate the registry so
  ``hermes health`` (no arg) enumerates every registered check. Today
  the registry has two entries (``telegram``, ``slack``); the shape
  anticipates Healthchecks, Sentry, and the exchange adapters joining
  later.
- **Wiring layer**: this module lives under ``entrypoints/`` so it MAY
  import ``adapters``, ``application``, and ``infrastructure`` (per
  import-linter contract ``adapters-never-import-entrypoints`` — the
  arrow points the other way). Adapters + config get composed here;
  the CLI stays thin.
- **UTC-only timestamps**: every ``last_check`` field is built via
  :func:`datetime.now` with an explicit ``tz=UTC`` argument. Naive
  datetimes are a code-conventions §2 violation.
- **Exit codes**: distinguish "not configured" (3) from "adapter down"
  (1). Scripts wrapping ``hermes health`` can react differently — a
  fresh clone missing ``.env.local`` should not page an operator.
- **Read-only probes**: every probe hits a read-only third-party
  endpoint (Telegram ``getMe``, Slack ``auth.test``). NEVER call a
  send endpoint (``sendMessage``, ``chat.postMessage``) from a health
  path — a watchdog script would spam the channel on every tick.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from hermes.adapters.notifier.slack_adapter import (
    SlackAdapter,
    SlackConfig,
    SlackNotifierError,
)
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)

__all__ = [
    "EXIT_FAIL",
    "EXIT_NOT_CONFIGURED",
    "EXIT_OK",
    "HealthCheckResult",
    "HealthStatus",
    "check_slack",
    "check_telegram",
    "get_check",
    "registered_integrations",
]

# Exit-code constants — kept as module-level so both the CLI dispatcher
# and the tests can import them without depending on magic numbers.
EXIT_OK: Final[int] = 0
EXIT_FAIL: Final[int] = 1
EXIT_NOT_CONFIGURED: Final[int] = 3


class HealthStatus:
    """String status labels used in printed tables.

    Constants — no need for a StrEnum. Two labels today; PR E may add
    ``DEGRADED`` when integrations grow richer.
    """

    OK: Final[str] = "OK"
    FAIL: Final[str] = "FAIL"


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Outcome of one integration probe.

    Attributes:
        integration: Short label — ``"telegram"``, ``"slack"``, etc.
        status: One of :class:`HealthStatus` labels.
        latency_ms: Wall-clock time the probe took, milliseconds.
        detail: Free-form context (bot username / workspace on OK,
            error message on FAIL). Included in the printed table.
        last_check: UTC-aware timestamp — when the probe ran.
        exit_code: Suggested process exit code (0 / 1 / 3).
    """

    integration: str
    status: str
    latency_ms: float
    detail: str
    last_check: datetime
    exit_code: int


def _now_utc() -> datetime:
    """Return the current UTC-aware datetime.

    Wrapped for testability — freezegun can patch the module-local
    attribute in a single monkeypatch call. Never returns a naive
    datetime.
    """
    return datetime.now(UTC)


def check_telegram(env: dict[str, str]) -> HealthCheckResult:
    """Probe the Telegram Bot API ``getMe`` endpoint.

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            supply a controlled dict so the ambient shell never leaks in.

    Returns:
        A :class:`HealthCheckResult` describing the probe outcome. The
        function does NOT raise on failure — the ``exit_code`` field
        carries the intent instead so the caller can print a table
        before exiting.
    """
    # Config-missing path — distinct exit code (3) so wrapper scripts
    # can distinguish "not configured yet" from "adapter is down".
    try:
        config = TelegramConfig.from_env(env)
    except TelegramNotifierError as exc:
        detail = (
            f"{exc.msg} — populate HERMES_TELEGRAM__BOT_TOKEN and "
            f"HERMES_TELEGRAM__CHAT_ID in .env.local "
            f"(see .env.local.example line 27-28)"
        )
        return HealthCheckResult(
            integration="telegram",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=detail,
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )

    adapter = TelegramAdapter.create(config)
    start = time.perf_counter()
    try:
        result = adapter.get_me()
    except TelegramNotifierError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return HealthCheckResult(
            integration="telegram",
            status=HealthStatus.FAIL,
            latency_ms=elapsed_ms,
            detail=str(exc),
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )
    finally:
        adapter.close()

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    username = result.get("username", "<unknown>")
    return HealthCheckResult(
        integration="telegram",
        status=HealthStatus.OK,
        latency_ms=elapsed_ms,
        detail=f"bot=@{username}",
        last_check=_now_utc(),
        exit_code=EXIT_OK,
    )


def check_slack(env: dict[str, str]) -> HealthCheckResult:
    """Probe the Slack Web API ``auth.test`` endpoint.

    ``auth.test`` is the canonical READ-ONLY validation call: it
    returns the workspace name (``team``) and bot user (``user``) tied
    to the token WITHOUT sending anything to any channel. Never call
    ``chat.postMessage`` from a health path — a watchdog loop would
    spam the channel every tick.

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            supply a controlled dict so the ambient shell never leaks in.

    Returns:
        A :class:`HealthCheckResult` describing the probe outcome. The
        function does NOT raise on failure — the ``exit_code`` field
        carries the intent instead so the caller can print a table
        before exiting.
    """
    # Config-missing path — distinct exit code (3) so wrapper scripts
    # can distinguish "not configured yet" from "adapter is down".
    try:
        config = SlackConfig.from_env(env)
    except SlackNotifierError as exc:
        detail = (
            f"{exc.msg} — populate HERMES_SLACK__BOT_TOKEN and "
            f"HERMES_SLACK__DEFAULT_CHANNEL in .env.local "
            f"(see .env.local.example)"
        )
        return HealthCheckResult(
            integration="slack",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=detail,
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )

    adapter = SlackAdapter.create(config)
    start = time.perf_counter()
    try:
        result = adapter.auth_test()
    except SlackNotifierError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return HealthCheckResult(
            integration="slack",
            status=HealthStatus.FAIL,
            latency_ms=elapsed_ms,
            detail=str(exc),
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )
    finally:
        adapter.close()

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    team = result.get("team", "<unknown>")
    user = result.get("user", "<unknown>")
    return HealthCheckResult(
        integration="slack",
        status=HealthStatus.OK,
        latency_ms=elapsed_ms,
        detail=f"workspace={team} bot=@{user}",
        last_check=_now_utc(),
        exit_code=EXIT_OK,
    )


# Registry — PR E will consolidate this into a shared discovery layer
# used by both `hermes health` (no arg → run every check) and any future
# `hermes doctor` subcommand. Today the shape anticipates Healthchecks,
# Sentry, and the exchange adapters joining as siblings.
_REGISTRY: Final[dict[str, Callable[[dict[str, str]], HealthCheckResult]]] = {
    "slack": check_slack,
    "telegram": check_telegram,
}


def registered_integrations() -> tuple[str, ...]:
    """Return the sorted tuple of integration names known to the registry.

    Returns:
        Tuple of integration labels in stable (sorted) order.
    """
    return tuple(sorted(_REGISTRY))


def get_check(name: str) -> Callable[[dict[str, str]], HealthCheckResult] | None:
    """Return the probe callable for ``name`` or ``None`` if unregistered.

    Args:
        name: Integration label — lowercase, matches CLI argument
            (e.g. ``"telegram"``, ``"slack"``).

    Returns:
        The probe callable, or ``None`` when ``name`` is not registered.
    """
    return _REGISTRY.get(name)


def render_table(result: HealthCheckResult, stream: Any) -> None:
    """Print ``result`` as a two-column key/value table to ``stream``.

    Kept intentionally minimal — no ``rich`` dependency, no ANSI
    colours. The CLI is invoked by systemd + CI runners that would
    strip escapes anyway; a plain table stays readable in journald.

    Args:
        result: The health-check outcome to render.
        stream: File-like object with a ``.write`` method.
    """
    rows = [
        ("integration", result.integration),
        ("status", result.status),
        ("latency_ms", f"{result.latency_ms:.1f}"),
        ("detail", result.detail),
        ("last_check", result.last_check.isoformat()),
    ]
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        stream.write(f"  {key.ljust(width)}  {value}\n")
