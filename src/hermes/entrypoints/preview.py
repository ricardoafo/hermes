"""`hermes preview` — inspect message templates without hitting real APIs.

Three subcommands:

- ``list`` — enumerate every event name supported by the template
  registry, with a short description.
- ``<event-name>`` — render Telegram + Slack outputs for a fixture event
  and print them, for every event whether or not the routing table fires
  that channel (the routing and the push loudness are printed above).
  Attachments (if any) are saved to
  :func:`tempfile.gettempdir` under a discoverable filename.
- ``<event-name> --send`` — same as above, PLUS actually send the
  rendered message through the wired-up Telegram + Slack adapters
  (whichever the routing table asks for). Refuses to send when
  ``HERMES_ENV == "prod"`` — never spam prod channels from a dev preview.
- ``<event-name> --fixture <path>`` — load event data from a JSON fixture
  file instead of the default sample.

Design notes
------------

- **argparse (stdlib)**: the wider hermes codebase already imports
  ``typer`` for future entrypoints, but this preview command has zero
  need for typer's Click magic. Argparse keeps the dep footprint zero
  and startup ~50ms faster than a typer app.
- **Domain-purity via composition**: fixtures build concrete
  ``DomainEvent`` instances, then dispatch through
  ``application/messages``. The CLI never imports ``adapters/`` directly
  when running in dry-run mode; the ``--send`` path imports them under
  a runtime guard so the ``list`` command works even in an environment
  without ``httpx`` set up.
- **Prod safety**: the ``--send`` handler reads ``HERMES_ENV`` via
  :func:`os.environ.get` (dotenv is loaded by ``load_config``). If the
  value is ``"prod"`` the send is refused with exit code 3.

Reference: 2026-08-23-slack-alongside-telegram.md; ADR-003 §04 (env
    tiering); code-conventions.md §1 (no ambient state).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, TextIO

from hermes.application.messages import (
    SlackMessage,
    slack_template,
    telegram_template,
)
from hermes.application.notification_router import EventClass, channels_for, is_event_loud
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.strategy_resumed import StrategyResumed
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.domain.value_objects.weight import Weight
from hermes.entrypoints._streams import resolve_err

__all__ = ["build_parser", "list_events", "main", "run_preview"]


# ---------------------------------------------------------------------------
# Event catalog + fixture factories. One entry per event name.
# ---------------------------------------------------------------------------

_SAMPLE_TIMESTAMP: Final[datetime] = datetime(2026, 8, 23, 14, 22, 3, tzinfo=UTC)
_SAMPLE_CORRELATION: Final[str] = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_SAMPLE_PAIR: Final[Pair] = Pair(base=Currency.BTC, quote=Currency.USDT)


def _fixture_signal_entry() -> SignalEmitted:
    """Return a canned entry SignalEmitted sample event."""
    return SignalEmitted(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=_SAMPLE_PAIR,
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal("61234.5"),
        stop_price=Decimal("59100"),
        atr_context=Decimal("1180"),
    )


def _fixture_signal_exit() -> SignalEmitted:
    """Return a canned exit SignalEmitted sample event."""
    return SignalEmitted(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=_SAMPLE_PAIR,
        kind=SignalKind.EXIT,
        side=SignalSide.LONG,
        entry_price=Decimal("61234.5"),
        stop_price=Decimal("62800"),
        atr_context=Decimal("1180"),
        exit_reason="trailing_stop",
    )


def _fixture_strategy_paused() -> StrategyPaused:
    """Return a canned StrategyPaused sample event."""
    return StrategyPaused(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        strategy_id="swing.v1",
        reason="operator kill switch",
        paused_at=_SAMPLE_TIMESTAMP,
        severity=AnomalySeverity.CRITICAL,
    )


def _fixture_strategy_resumed() -> StrategyResumed:
    """Return a canned StrategyResumed sample event."""
    return StrategyResumed(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        strategy_id="swing.v1",
        resumed_at=_SAMPLE_TIMESTAMP,
        pause_duration_seconds=900,
    )


def _fixture_balance_low() -> BalanceLow:
    """Return a canned BalanceLow sample event."""
    return BalanceLow(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        asset=Currency.USDT,
        current=Money(amount=Decimal("50"), currency=Currency.USDT),
        threshold=Money(amount=Decimal("100"), currency=Currency.USDT),
        checked_at=_SAMPLE_TIMESTAMP,
    )


def _fixture_trade_rejected() -> TradeRejected:
    """Return a canned TradeRejected sample event."""
    return TradeRejected(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        pair=_SAMPLE_PAIR,
        side=TradeSide.BUY,
        rejection_reason="insufficient balance",
        exchange="binance",
        attempted_at=_SAMPLE_TIMESTAMP,
    )


def _fixture_anomaly_detected() -> AnomalyDetected:
    """Return a canned AnomalyDetected sample event."""
    return AnomalyDetected(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        source="swing.risk",
        severity=AnomalySeverity.CRITICAL,
        reason="ledger reconciliation mismatch",
    )


def _fixture_trade_executed() -> TradeExecuted:
    """Return a canned TradeExecuted sample event (POSITION_LARGE-shaped)."""
    return TradeExecuted(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        pair=_SAMPLE_PAIR,
        side=TradeSide.BUY,
        qty=Quantity(amount=Decimal("0.5"), asset=Currency.BTC),
        price=Price(amount=Decimal("68000"), pair=_SAMPLE_PAIR),
        fee=Money(amount=Decimal("34"), currency=Currency.USDT),
        strategy_id=StrategyId("swing-donchian-4h"),
    )


def _fixture_rebalance_triggered() -> RebalanceTriggered:
    """Return a canned RebalanceTriggered sample event."""
    return RebalanceTriggered(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        pair=_SAMPLE_PAIR,
        current_weight=Weight(value=Decimal("0.35")),
        target_weight=Weight(value=Decimal("0.25")),
        band=Weight(value=Decimal("0.05")),
    )


def _fixture_daily_trade_digest() -> DailyTradeDigest:
    """Return a canned DailyTradeDigest sample event."""
    winner = TradeSummary(
        pair=Pair(base=Currency.BTC, quote=Currency.USDT),
        pnl=Money(amount=Decimal("120.00"), currency=Currency.EUR),
    )
    loser = TradeSummary(
        pair=Pair(base=Currency.ETH, quote=Currency.USDT),
        pnl=Money(amount=Decimal("-45.50"), currency=Currency.EUR),
    )
    return DailyTradeDigest(
        occurred_at=_SAMPLE_TIMESTAMP,
        correlation_id=_SAMPLE_CORRELATION,
        event_seq=0,
        day=date(2026, 8, 23),
        total_trades=12,
        filled=10,
        rejected=2,
        total_notional=Money(amount=Decimal("12500.50"), currency=Currency.EUR),
        top_winners=(winner,),
        top_losers=(loser,),
    )


# Registry: event-name -> (description, fixture factory, routing category)
_CATALOG: Final[dict[str, tuple[str, Callable[[], DomainEvent], EventClass]]] = {
    "signal-emitted": (
        "Strategy entry signal with its stop (the only event live in production)",
        _fixture_signal_entry,
        EventClass.SIGNAL_EMITTED,
    ),
    "signal-exit": (
        "Strategy exit signal with the rule that fired",
        _fixture_signal_exit,
        EventClass.SIGNAL_EMITTED,
    ),
    "strategy-paused": (
        "Strategy halted — critical drawdown or operator kill",
        _fixture_strategy_paused,
        EventClass.STRATEGY_PAUSED,
    ),
    "strategy-resumed": (
        "Strategy back online after a pause",
        _fixture_strategy_resumed,
        EventClass.STRATEGY_RESUMED,
    ),
    "balance-low": (
        "Wallet balance below the configured threshold",
        _fixture_balance_low,
        EventClass.BALANCE_LOW,
    ),
    "trade-rejected": (
        "Exchange refused an order (single-instance sample)",
        _fixture_trade_rejected,
        EventClass.TRADE_REJECTED_ESCALATED,
    ),
    "anomaly-detected": (
        "Circuit-breaker / invariant violation",
        _fixture_anomaly_detected,
        EventClass.ANOMALY_DETECTED,
    ),
    "position-large": (
        "Large single-fill trade (POSITION_LARGE classification)",
        _fixture_trade_executed,
        EventClass.POSITION_LARGE,
    ),
    "rebalance-triggered": (
        "Rebalancer detected drift beyond band",
        _fixture_rebalance_triggered,
        EventClass.REBALANCE_TRIGGERED,
    ),
    "daily-trade-digest": (
        "End-of-day trading summary with CSV attachment",
        _fixture_daily_trade_digest,
        EventClass.DAILY_TRADE_DIGEST,
    ),
}


# ---------------------------------------------------------------------------
# Fixture loader — JSON → concrete event via per-name reconstruction.
# ---------------------------------------------------------------------------

# We intentionally do NOT ship a generic JSON→event decoder. A per-name
# reconstruction is more verbose but preserves domain-side validation
# (e.g. BalanceLow's tri-currency invariant runs on every custom fixture).


def _load_fixture(name: str, path: Path) -> DomainEvent:
    """Load a JSON fixture from ``path`` and reconstruct the event for ``name``.

    Args:
        name: Event name (kebab-case) — must exist in :data:`_CATALOG`.
        path: JSON file to load. Top-level MUST be an object mapping
            field names to values. Unknown fields raise ``ValueError``.

    Returns:
        A concrete :class:`DomainEvent` instance.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the JSON is invalid, ``name`` is unknown, or the
            reconstruction fails a domain invariant.
    """
    if name not in _CATALOG:
        raise ValueError(f"Unknown event name: {name!r} — see `hermes preview list`.")
    raw = path.read_text(encoding="utf-8")
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Fixture {path} must contain a JSON object at the top level.")
    return _reconstruct(name, data)


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 UTC datetime string into a tz-aware datetime.

    Args:
        value: ISO-8601 datetime string.

    Returns:
        Timezone-aware UTC datetime.

    Raises:
        ValueError: On malformed input.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"Fixture timestamp {value!r} is naive — must be UTC-aware ISO-8601.")
    return parsed.astimezone(UTC)


def _parse_money(value: dict[str, Any]) -> Money:
    """Parse a ``{"amount": "...", "currency": "..."}`` object into Money."""
    return Money(amount=Decimal(str(value["amount"])), currency=Currency(value["currency"]))


def _parse_pair(value: str) -> Pair:
    """Parse a ``"BTC/USDT"`` string into a Pair."""
    base, quote = value.split("/", 1)
    return Pair(base=Currency(base), quote=Currency(quote))


def _reconstruct(name: str, data: dict[str, Any]) -> DomainEvent:  # noqa: PLR0911
    """Rebuild the event class matching ``name`` from a JSON dict.

    Args:
        name: Event name from :data:`_CATALOG`.
        data: Parsed JSON object with field names matching the event.

    Returns:
        A concrete event instance.
    """
    occurred_at = _parse_ts(data["occurred_at"])
    correlation_id = str(data["correlation_id"])
    event_seq = int(data.get("event_seq", 0))
    if name in ("signal-emitted", "signal-exit"):
        entry_price = data.get("entry_price")
        return SignalEmitted(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            strategy_id=StrategyId(str(data["strategy_id"])),
            pair=_parse_pair(data["pair"]),
            kind=SignalKind(data["kind"]),
            side=SignalSide(data["side"]),
            entry_price=None if entry_price is None else Decimal(str(entry_price)),
            stop_price=Decimal(str(data["stop_price"])),
            atr_context=Decimal(str(data["atr_context"])),
            exit_reason=data.get("exit_reason"),
        )
    if name == "strategy-paused":
        return StrategyPaused(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            strategy_id=str(data["strategy_id"]),
            reason=str(data["reason"]),
            paused_at=_parse_ts(data["paused_at"]),
            severity=AnomalySeverity(data["severity"]),
        )
    if name == "strategy-resumed":
        return StrategyResumed(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            strategy_id=str(data["strategy_id"]),
            resumed_at=_parse_ts(data["resumed_at"]),
            pause_duration_seconds=int(data["pause_duration_seconds"]),
        )
    if name == "balance-low":
        asset = Currency(data["asset"])
        return BalanceLow(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            asset=asset,
            current=_parse_money(data["current"]),
            threshold=_parse_money(data["threshold"]),
            checked_at=_parse_ts(data["checked_at"]),
        )
    if name == "trade-rejected":
        return TradeRejected(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            pair=_parse_pair(data["pair"]),
            side=TradeSide(data["side"]),
            rejection_reason=str(data["rejection_reason"]),
            exchange=str(data["exchange"]),
            attempted_at=_parse_ts(data["attempted_at"]),
        )
    if name == "anomaly-detected":
        return AnomalyDetected(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            source=str(data["source"]),
            severity=AnomalySeverity(data["severity"]),
            reason=str(data["reason"]),
        )
    if name == "position-large":
        pair = _parse_pair(data["pair"])
        return TradeExecuted(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            pair=pair,
            side=TradeSide(data["side"]),
            qty=Quantity(amount=Decimal(str(data["qty"])), asset=pair.base),
            price=Price(amount=Decimal(str(data["price"])), pair=pair),
            fee=_parse_money(data["fee"]),
            strategy_id=StrategyId(str(data.get("strategy_id", "swing-donchian-4h"))),
        )
    if name == "rebalance-triggered":
        return RebalanceTriggered(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            pair=_parse_pair(data["pair"]),
            current_weight=Weight(value=Decimal(str(data["current_weight"]))),
            target_weight=Weight(value=Decimal(str(data["target_weight"]))),
            band=Weight(value=Decimal(str(data["band"]))),
        )
    if name == "daily-trade-digest":
        winners = tuple(
            TradeSummary(pair=_parse_pair(w["pair"]), pnl=_parse_money(w["pnl"]))
            for w in data.get("top_winners", [])
        )
        losers = tuple(
            TradeSummary(pair=_parse_pair(l_["pair"]), pnl=_parse_money(l_["pnl"]))
            for l_ in data.get("top_losers", [])
        )
        return DailyTradeDigest(
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            event_seq=event_seq,
            day=date.fromisoformat(data["day"]),
            total_trades=int(data["total_trades"]),
            filled=int(data["filled"]),
            rejected=int(data["rejected"]),
            total_notional=_parse_money(data["total_notional"]),
            top_winners=winners,
            top_losers=losers,
        )
    # Guarded above by the ValueError in _load_fixture; unreachable in practice.
    raise ValueError(f"Unknown event name: {name!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def list_events(stream: Any) -> int:
    """Print every event name + description to ``stream``.

    Args:
        stream: Any file-like object with a ``write`` method
            (``sys.stdout`` in production; ``io.StringIO`` in tests).

    Returns:
        Exit code 0.
    """
    lines = ["Available events for `hermes preview`:", ""]
    for name in sorted(_CATALOG):
        description, _factory, _cls = _CATALOG[name]
        lines.append(f"  {name:22s} {description}")
    lines.append("")
    stream.write("\n".join(lines))
    return 0


def _write_attachments(message: SlackMessage, event_name: str) -> list[Path]:
    """Save every Slack attachment to a temp file and return the paths.

    Args:
        message: The Slack message whose attachments to persist.
        event_name: Event name used to build a discoverable filename.

    Returns:
        List of paths written (empty when the message has no attachments).
    """
    written: list[Path] = []
    tmpdir = Path(tempfile.gettempdir())
    for attachment in message.attachments:
        target = tmpdir / f"hermes-preview-{event_name}-{attachment.name}"
        target.write_bytes(attachment.content)
        written.append(target)
    return written


def run_preview(
    event_name: str, *, fixture: Path | None, stream: Any, err: TextIO | None = None
) -> int:
    """Render templates for ``event_name`` and print them to ``stream``.

    Args:
        event_name: Kebab-case event name from :data:`_CATALOG`.
        fixture: Optional JSON fixture path — when provided, overrides
            the canned sample factory.
        stream: File-like output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Exit code 0 on success, 2 on unknown event name, 1 on fixture
        load failure.
    """
    err = resolve_err(err)
    if event_name not in _CATALOG:
        err.write(
            f"ERROR: unknown event name {event_name!r}. "
            f"Run `hermes preview list` for the catalog.\n"
        )
        return 2
    if fixture is not None:
        try:
            event = _load_fixture(event_name, fixture)
        except (FileNotFoundError, ValueError) as exc:
            err.write(f"ERROR: could not load fixture {fixture}: {exc}\n")
            return 1
    else:
        _description, factory, _cls = _CATALOG[event_name]
        event = factory()

    _description, _factory, event_class = _CATALOG[event_name]
    telegram_fires, slack_fires = channels_for(event_class)

    stream.write(f"# hermes preview — {event_name}\n\n")
    push = "loud (rings the phone)" if is_event_loud(event) else "silent (disable_notification)"
    stream.write(f"## Delivery\nTelegram push: {push}\n\n")

    stream.write("## Telegram (MarkdownV2)\n")
    if not telegram_fires:
        stream.write("(routing table: Telegram does not fire for this event; shown for review)\n")
    try:
        telegram_text = telegram_template(event)
    except ValueError:
        telegram_text = "(Telegram has no template for this event: Slack-only report)"
    stream.write(f"{telegram_text}\n\n")

    slack_message = slack_template(event)
    attachments_written = _write_attachments(slack_message, event_name)
    stream.write("## Slack — text fallback\n")
    if not slack_fires:
        stream.write("(routing table: Slack does not fire for this event; shown for review)\n")
    stream.write(f"{slack_message.text}\n\n")
    stream.write("## Slack — Block Kit\n")
    stream.write(json.dumps(slack_message.blocks, indent=2, ensure_ascii=False))
    stream.write("\n\n")
    stream.write("## Slack — attachments\n")
    if slack_message.attachments:
        for attachment, path in zip(slack_message.attachments, attachments_written, strict=True):
            stream.write(
                f"- {attachment.name} ({attachment.mime}, "
                f"{len(attachment.content)} bytes) saved to {path}\n"
            )
    else:
        stream.write("(none)\n")
    return 0


def _refuse_prod_send() -> bool:
    """Return True when the current HERMES_ENV is 'prod'.

    Reading ``os.environ`` at call time (not import time) lets tests
    monkey-patch the environment with :func:`monkeypatch.setenv` and get
    the expected refusal behaviour.
    """
    return os.environ.get("HERMES_ENV", "").strip().lower() == "prod"


def run_send(  # pragma: no cover
    event_name: str, *, fixture: Path | None, stream: Any, err: TextIO | None = None
) -> int:
    """Render + actually send the rendered message via the wired adapters.

    Refuses to run when ``HERMES_ENV == "prod"`` — never spam prod
    channels from a dev preview. The refusal check is unit-testable
    via :func:`_refuse_prod_send`; the send path itself is only
    exercised in adapter-owning follow-up PRs (B + C).

    Args:
        event_name: Kebab-case event name.
        fixture: Optional JSON fixture path.
        stream: File-like output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Exit code 0 on successful send; 3 on prod refusal.
    """
    _ = event_name, fixture, stream  # retained for API symmetry with run_preview
    err = resolve_err(err)
    if _refuse_prod_send():
        err.write(
            "ERROR: --send refuses to run when HERMES_ENV=prod. "
            "Never spam prod channels from a dev preview.\n"
        )
        return 3
    err.write(
        "ERROR: --send requires the Telegram + Slack adapters (PRs B + C). "
        "Only prod-refusal is implemented in this scaffolding PR.\n"
    )
    return 4


# ---------------------------------------------------------------------------
# Argparse plumbing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the argparse parser for the ``preview`` subcommand tree.

    Returns:
        A configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="hermes preview",
        description="Preview notification templates without hitting real APIs.",
    )
    parser.add_argument(
        "event",
        nargs="?",
        default=None,
        help="Event name (kebab-case) or 'list' to enumerate all events.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Optional JSON fixture file to load instead of the canned sample.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send the rendered message through the configured adapters.",
    )
    return parser


def main(
    argv: list[str] | None = None, *, stream: Any | None = None, err: TextIO | None = None
) -> int:
    """Entry point for `hermes preview`.

    Args:
        argv: Argument list — defaults to ``sys.argv[1:]``.
        stream: Output stream — defaults to ``sys.stdout``. Injected
            for tests.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code (0 = success).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stream if stream is not None else sys.stdout

    if args.event is None or args.event == "list":
        return list_events(out)
    if args.send:
        return run_send(args.event, fixture=args.fixture, stream=out, err=err)
    return run_preview(args.event, fixture=args.fixture, stream=out, err=err)
