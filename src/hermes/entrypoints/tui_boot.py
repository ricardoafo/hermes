"""The console's cold start screen (#1020).

On start the console shows the Winged H, the running release and one row
per start-up check (ledger, engine, journal, market feed, AI supervisor)
over Home. Each row turns ✓ with what the check found or ✗ with why it
failed, and the screen closes on its own shortly after the last one. Any
key closes it at once; with motion off it is never shown.

The checks are real reads, so the screen doubles as a connection check.
They run in a worker thread, never on the UI thread, and a failed check
never stops the console: Home shows the same failure on its own panel.

Reference: openspec/changes/1020-cold-start/change.md; the design spec's
"Start-up sequence" (Look A).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import tzinfo
from typing import Final

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from hermes.application.console import ConsoleReadModel, HeartbeatState
from hermes.application.console.logs import LogLine
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.tui_logo import Logo
from hermes.entrypoints.tui_theme import Tokens
from hermes.ports.live_prices import LiveTickerPort

__all__ = [
    "BOOT_HOLD_SECONDS",
    "BootCheck",
    "BootResult",
    "BootScreen",
    "boot_checks",
    "boot_line",
    "run_check",
]

# How long the finished checklist stays up before Home shows.
BOOT_HOLD_SECONDS: Final = 0.8
_LABEL: Final = 15
_FEED_PAIR: Final = Pair(base=Currency.BTC, quote=Currency.USDT)


class BootCheckFailedError(Exception):
    """A start-up check found a problem; the message says what, in the operator's words."""


@dataclass(frozen=True, slots=True)
class BootCheck:
    """One start-up check.

    Attributes:
        label: The row's name (``ledger``).
        run: Performs the check and returns what it found; raises on a problem.
    """

    label: str
    run: Callable[[], str]


@dataclass(frozen=True, slots=True)
class BootResult:
    """What one start-up check found.

    Attributes:
        label: The row's name.
        ok: The check passed.
        detail: What it found, or why it failed.
    """

    label: str
    ok: bool
    detail: str


def run_check(check: BootCheck) -> BootResult:
    """Run one check; any exception becomes a failed row, never a crash.

    Args:
        check: The check to run.
    """
    try:
        detail = check.run()
    except Exception as exc:
        return BootResult(check.label, ok=False, detail=str(exc) or type(exc).__name__)
    return BootResult(check.label, ok=True, detail=detail)


def boot_checks(
    model: ConsoleReadModel,
    *,
    tz: tzinfo,
    journal: Callable[[int], Sequence[LogLine] | None] | None,
    ticker: LiveTickerPort | None,
) -> tuple[BootCheck, ...]:
    """Return the start-up checks, in the order they show.

    Args:
        model: The read model the console draws from.
        tz: Time zone for the times the rows name.
        journal: The journal source; ``None`` when there is none.
        ticker: The live ticker; ``None`` for demo data.
    """

    def ledger() -> str:
        if not model.market_watch():
            first = model.heartbeat().next_check_at.astimezone(tz)
            return f"first run · first bar check due {first:%H:%M}"
        return f"last bar check {model.heartbeat().last_check_at.astimezone(tz):%H:%M}"

    def engine() -> str:
        heartbeat = model.heartbeat()
        if heartbeat.state is not HeartbeatState.OK:
            msg = f"{heartbeat.state.value.lower()} · next check was due "
            msg += f"{heartbeat.next_check_at.astimezone(tz):%H:%M}"
            raise BootCheckFailedError(msg)
        return f"on time · next check {heartbeat.next_check_at.astimezone(tz):%H:%M}"

    def journal_lines() -> str:
        lines = None if journal is None else journal(1)
        if lines is None:
            msg = "not readable here"
            raise BootCheckFailedError(msg)
        return "readable"

    def market_feed() -> str:
        if ticker is None:
            msg = "demo data · no live prices"
            raise BootCheckFailedError(msg)
        ticker.get_ticker(_FEED_PAIR)
        return "Kraken prices every 10 s"

    def supervisor() -> str:
        briefs = model.briefs()
        return f"{len(briefs)} brief{'' if len(briefs) == 1 else 's'} published"

    return (
        BootCheck("ledger", ledger),
        BootCheck("engine", engine),
        BootCheck("journal", journal_lines),
        BootCheck("market feed", market_feed),
        BootCheck("AI supervisor", supervisor),
    )


def boot_line(label: str, result: BootResult | None, tokens: Tokens) -> Text:
    """Return one checklist row: pending, passed or failed.

    Args:
        label: The row's name.
        result: The check's result; ``None`` while it runs.
        tokens: The theme's token sheet.
    """
    line = Text()
    if result is None:
        line.append("…  ", style=tokens.muted)
        line.append(f"{label:<{_LABEL}}", style=tokens.text)
        line.append("checking", style=tokens.muted)
        return line
    mark, colour = ("✓", tokens.live) if result.ok else ("✗", tokens.warn)
    line.append(f"{mark}  ", style=f"bold {colour}")
    line.append(f"{label:<{_LABEL}}", style=tokens.text)
    line.append(result.detail, style=tokens.text if result.ok else colour)
    return line


class BootScreen(Screen[None]):
    """The cold start screen: logo, release and the start-up checklist."""

    DEFAULT_CSS = """
    BootScreen { background: $hud-void; align: center middle; }
    BootScreen > #boot-panel {
        width: auto; height: auto; padding: 1 3; background: $hud-overlay;
    }
    BootScreen #boot-release { color: $hud-muted; margin: 1 0; }
    BootScreen #boot-checks { width: auto; }
    BootScreen #boot-skip { color: $hud-dim; margin-top: 1; }
    """

    def __init__(
        self,
        checks: Sequence[BootCheck],
        tokens: Callable[[], Tokens],
        *,
        release: str | None = None,
        hold: float = BOOT_HOLD_SECONDS,
    ) -> None:
        """Hold the checks; they start when the screen mounts.

        Args:
            checks: The start-up checks, in order.
            tokens: Returns the current theme's token sheet.
            release: The running release's name; ``None`` omits the line.
            hold: Seconds the finished checklist stays up.
        """
        super().__init__()
        self._checks = tuple(checks)
        self._tokens = tokens
        self._release = release
        self._hold = hold
        self.results: list[BootResult | None] = [None] * len(self._checks)

    def compose(self) -> ComposeResult:
        with Vertical(id="boot-panel"):
            yield Logo()
            release = "release unknown" if self._release is None else f"release {self._release}"
            yield Static(release, id="boot-release")
            yield Static(self._checklist(), id="boot-checks")
            yield Static("any key opens the console · Q quits", id="boot-skip")

    def on_mount(self) -> None:
        """Start the checks in a worker thread."""
        self.run_worker(self._run_checks, thread=True, exclusive=True, group="boot")

    def _run_checks(self) -> None:
        for index, check in enumerate(self._checks):
            result = run_check(check)
            self.app.call_from_thread(self._show, index, result)
        self.app.call_from_thread(self.set_timer, self._hold, self._close)

    def _show(self, index: int, result: BootResult) -> None:
        self.results[index] = result
        if self.is_mounted:
            self.query_one("#boot-checks", Static).update(self._checklist())

    def _checklist(self) -> Text:
        tokens = self._tokens()
        rows = [
            boot_line(check.label, result, tokens)
            for check, result in zip(self._checks, self.results, strict=True)
        ]
        return Text("\n").join(rows)

    def _close(self) -> None:
        if self.is_current:
            self.dismiss()

    def on_key(self, event: events.Key) -> None:
        """Q quits; any other key opens the console at once."""
        event.stop()
        if event.key in {"q", "Q"}:
            self.app.exit()
            return
        self._close()
