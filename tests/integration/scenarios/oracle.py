"""The rule check run after every bar serve handles on a generated path, and the run driver.

:class:`Oracle` keeps its own books: it replays every trade serve records on
the cash and positions the pre-registration starts from, marks the positions
with the closes of the bars the fake market sent, and after each bar compares
what serve stored in the ledger with that replay and with the rules below. It
never imports the sizing calculator, the fill model, the fee lookup or the
portfolio tracker: each limit is copied from the signed pre-registration
(``docs/experiments/swing-mvp1/pre-registration-v1.md``) through
:mod:`.expected`, or from a behaviour rule stated in code (``rules.py``).

Rules checked after every bar (``RULES`` names each one):

- cash: the ledger's cash is never negative.
- risk: an entry risks at most 1% of the experiment capital at its stop
  (§5.4 says capital, $50, not current equity).
- notional: an entry's notional is at most the 5% cap (§5.4).
- exposure: open positions at their marks plus the new entry's notional stay
  within 15% of capital (§5.5).
- stop: every open position has a stop recorded on each 4h bar, below the
  entry price on the entry bar, never lower than the bar before (stop ratchet),
  and a bar whose low reaches the stop signals the exit on that bar (§5.2).
- pairing: every BUY answers an entry signal of its bar on a pair not held;
  every SELL answers an exit signal of its bar and sells the whole position;
  no exit is signalled for a pair not held; a bar delivered again writes nothing.
- equity: the ledger's cash and positions equal the replay, and its equity is
  cash plus each position times its mark, to the cent.
- fee: every fee is price x quantity x the fee table's taker rate (§6.1).
- book: an entry fills only on a live depth snapshot taken at the bar, never
  while the depth endpoint is down (stale or missing book: no new entry); an
  exit signalled while serve has a live book fills on its bar.

After the run (:meth:`Oracle.finish`): every bar was processed once, in
order, and every boot exited cleanly except the documented stop on a bar
published after the next one. Restart equivalence (a restart changes nothing)
compares two runs of one path with :func:`fingerprint`.

Exit timing follows what shadow does (issue #785): the exit is a market sell
on the bar whose low reached the stop, not a resting stop order, so no fill
price is compared with the stop.

An equity mismatch that matches the open positions valued at their fill price
right after a boot is reported under ``marks-after-boot``, not ``equity``: a
suspected defect pinned by its own strict xfail. Not checked: the correlation
lock, spread spike and depeg rules and the time-to-recovery kill (issue #514:
serve does not feed them).
"""

from __future__ import annotations

import itertools
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from structlog.testing import capture_logs

from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from tests.integration.scenarios import expected
from tests.integration.scenarios.paths import GeneratedPath, Window
from tests.support.fake_market import harness

type Payload = dict[str, Any]
"""An event's JSON payload as the ledger stores it; its shape is the event's, read by key."""

_CENT = Decimal("0.01")
_FEE_PLACES = Decimal("1e-12")
"""Fees are compared to 12 places, as the scenario tests do: the fill model rounds below that."""

LIVE_BOOK_WINDOW = timedelta(minutes=5)
"""Shadow D-2: serve takes a live book only for a bar closed at most this long ago."""

STALE_BOOK_SECONDS = 2
"""RO-04: a book older than 2 seconds is stale (pre-reg §5.5 binding threshold)."""

FEE_TABLE: tuple[tuple[date, frozenset[str], Decimal], ...] = (
    (date(2024, 1, 1), frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"}), expected.TAKER_FEE),
)
"""§6.1 VIP0 taker rate by date and pair, from ``fee-history.csv``; BNB discount off."""

MARKS_AFTER_BOOT = "marks-after-boot"
"""The equity rule broken in one known way: see ``test_generated_paths.py``'s strict xfail."""

RULES = ("cash", "risk", "notional", "exposure", "stop", "pairing", "equity", "fee", "book")


def taker_rate(pair: str, on: date) -> Decimal | None:
    """Return the fee table's taker rate for ``pair`` on ``on``, or ``None`` with no row."""
    rows = [rate for since, pairs, rate in FEE_TABLE if since <= on and pair in pairs]
    return rows[-1] if rows else None


@dataclass
class _Held:
    qty: Decimal
    mark: Decimal
    marked_at: datetime
    entry_price: Decimal
    fill_price: Decimal
    filled_at: datetime
    last_stop: Decimal | None = None


@dataclass
class Oracle:
    """Replays serve's ledger after every bar and records each broken rule.

    Attributes:
        path: The generated path being run.
        violations: One line per broken rule: seed, bar number, rule, evidence.
        bars_seen: Bars serve's loop handled, over every boot.

    """

    path: GeneratedPath
    violations: list[str] = field(default_factory=list)
    bars_seen: int = 0
    cash: Decimal = expected.CAPITAL
    held: dict[str, _Held] = field(default_factory=dict)
    _cursor: dict[str, int] = field(
        default_factory=lambda: {"trades": 0, "events": 0, "portfolio_marks": 0, "stop_marks": 0}
    )
    _last_open: dict[tuple[str, str], datetime] = field(default_factory=dict)
    _connection: sqlite3.Connection | None = None
    _unmarked_since_boot: set[str] = field(default_factory=set)

    def after_bar(self, bar: Bar, now: datetime, connection: sqlite3.Connection) -> None:
        """Check every rule against what serve stored for ``bar``; never raise into serve."""
        self.bars_seen += 1
        if connection is not self._connection:
            self._connection = connection
            self._unmarked_since_boot = set(self.held)
        try:
            self._check(bar, now, connection)
        except Exception as exc:  # a crash here must be reported, not kill serve
            self._fail(bar, "oracle", f"could not check the bar: {exc!r}")

    def _fail(self, bar: Bar, rule: str, message: str) -> None:
        tag = f"{self.path.kind} seed {self.path.seed} bar {self.bars_seen}"
        where = f"{bar.pair} {bar.interval.value} close {bar.close_time.isoformat()}"
        self.violations.append(f"{tag} ({where}) {rule}: {message}")

    def _new(self, connection: sqlite3.Connection, table: str, columns: str) -> list[Any]:
        rows = connection.execute(
            f"SELECT rowid, {columns} FROM {table} WHERE rowid > ? ORDER BY rowid",  # noqa: S608
            (self._cursor[table],),
        ).fetchall()
        if rows:
            self._cursor[table] = rows[-1][0]
        return rows

    def _check(self, bar: Bar, now: datetime, connection: sqlite3.Connection) -> None:
        pair = f"{bar.pair.base.value}/{bar.pair.quote.value}"
        stream = (pair, bar.interval.value)
        last = self._last_open.get(stream)
        if last is not None and bar.open_time <= last:
            self._check_repeat(bar, connection)
            return
        self._last_open[stream] = bar.open_time
        events = self._new(connection, "events", "event_type, correlation_id, payload_json")
        signals = [json.loads(e[3]) for e in events if e[1] == "SignalEmitted"]
        quality = {e[2]: json.loads(e[3]) for e in events if e[1] == "FillQualityRecorded"}
        trades = self._new(
            connection,
            "trades",
            "correlation_id, pair_base || '/' || pair_quote, side, qty_amount, price_amount,"
            " fee_amount, executed_at",
        )
        held_before = set(self.held)
        self._check_signals(bar, pair, signals)
        sold = self._check_trades(bar, now, pair, signals, trades, quality)
        self._check_unfilled_exits(bar, now, pair, signals, sold)
        self._mark(bar, pair)
        self._check_portfolio(bar, connection)
        self._check_stops(bar, pair, connection, held_before, signals)

    def _check_repeat(self, bar: Bar, connection: sqlite3.Connection) -> None:
        """Check that a bar delivered again (a repeated klines row) writes nothing."""
        written = {
            table: len(self._new(connection, table, "1"))
            for table in ("trades", "events", "portfolio_marks", "stop_marks")
        }
        if any(written.values()):
            self._fail(bar, "pairing", f"a repeated bar wrote rows {written}")

    def _check_signals(self, bar: Bar, pair: str, signals: list[Payload]) -> None:
        for signal in signals:
            name = f"{signal['pair']['base']}/{signal['pair']['quote']}"
            at = datetime.fromisoformat(signal["occurred_at"])
            if (name, at) != (pair, bar.close_time):
                self._fail(
                    bar, "pairing", f"a {signal['kind']} signal of {name} at {at} on this bar"
                )
            elif signal["kind"] == "exit" and name not in self.held:
                self._fail(bar, "pairing", f"exit signalled for {name}, which is not held")
            elif signal["kind"] == "entry" and name in self.held:
                self._fail(bar, "pairing", f"entry signalled for {name}, which is already held")
            if signal["kind"] == "exit" and signal["exit_reason"] == "chandelier":
                stop = Decimal(signal["stop_price"])
                if bar.low > stop:
                    self._fail(bar, "stop", f"chandelier exit with low {bar.low} above stop {stop}")

    def _check_trades(
        self,
        bar: Bar,
        now: datetime,
        pair: str,
        signals: list[Payload],
        trades: list[Any],
        quality: dict[str, Payload],
    ) -> bool:
        kinds = {s["kind"]: s for s in signals}
        sold = False
        for _, correlation, name, side, qty_text, price_text, fee_text, at_text in trades:
            qty, price, fee = Decimal(qty_text), Decimal(price_text), Decimal(fee_text)
            at = datetime.fromisoformat(at_text)
            if name != pair:
                self._fail(bar, "pairing", f"a {side} of {name} recorded on this bar")
                continue
            self._check_fee(bar, name, at, qty, price, fee)
            if side == "BUY":
                self._check_entry(bar, now, name, kinds.get("entry"), qty, quality.get(correlation))
                cost = qty * price + fee
                if cost > self.cash:
                    self._fail(bar, "cash", f"BUY costs {cost} with {self.cash} cash")
                self.cash -= cost
                entry = kinds.get("entry")
                self.held[name] = _Held(
                    qty=qty,
                    mark=price,
                    marked_at=at,
                    entry_price=Decimal(entry["entry_price"]) if entry else price,
                    fill_price=price,
                    filled_at=at,
                )
            else:
                sold = True
                held = self.held.pop(name, None)
                if "exit" not in kinds:
                    self._fail(bar, "pairing", "a SELL without an exit signal on its bar")
                if held is None:
                    self._fail(bar, "pairing", f"a SELL of {qty} {name} with no position open")
                elif qty != held.qty:
                    self._fail(bar, "pairing", f"SELL of {qty} {name} with {held.qty} held")
                self.cash += qty * price - fee
            if self.cash < 0:
                self._fail(bar, "cash", f"replayed cash is {self.cash}")
        return sold

    def _check_fee(
        self, bar: Bar, pair: str, at: datetime, qty: Decimal, price: Decimal, fee: Decimal
    ) -> None:
        rate = taker_rate(pair, at.date())
        if rate is None:
            self._fail(bar, "fee", f"no fee table row for {pair} on {at.date()}")
            return
        want = (price * qty * rate).quantize(_FEE_PLACES)
        if fee.quantize(_FEE_PLACES) != want:
            self._fail(bar, "fee", f"fee {fee} on {qty} at {price}; the table gives {want}")

    def _check_entry(
        self,
        bar: Bar,
        now: datetime,
        pair: str,
        entry: Payload | None,
        qty: Decimal,
        quality: Payload | None,
    ) -> None:
        if entry is None:
            self._fail(bar, "pairing", "a BUY without an entry signal on its bar")
            return
        if pair in self.held:
            self._fail(bar, "pairing", f"a BUY of {pair}, which is already held")
        signal_price, stop = Decimal(entry["entry_price"]), Decimal(entry["stop_price"])
        risk = qty * (signal_price - stop)
        if risk > expected.RISK_BUDGET:
            self._fail(bar, "risk", f"risks {risk} at stop {stop}, budget {expected.RISK_BUDGET}")
        notional = qty * signal_price
        if notional > expected.POSITION_CAP:
            self._fail(bar, "notional", f"notional {notional} over {expected.POSITION_CAP}")
        open_value = sum((h.qty * h.mark for h in self.held.values()), Decimal(0))
        if open_value + notional > expected.EXPOSURE_CAP:
            self._fail(
                bar,
                "exposure",
                f"open {open_value} + new {notional} over {expected.EXPOSURE_CAP}",
            )
        if _inside(now, self.path.book_outages):
            self._fail(bar, "book", f"entry filled at {now} while the depth endpoint was down")
        if quality is None:
            self._fail(bar, "book", "entry fill has no fill-quality row")
            return
        book_time = datetime.fromisoformat(quality["book_time"])
        if quality["book_source"] != "live" or now - book_time > timedelta(
            seconds=STALE_BOOK_SECONDS
        ):
            self._fail(
                bar,
                "book",
                f"entry filled on a {quality['book_source']} book stamped {book_time}, now {now}",
            )

    def _check_unfilled_exits(
        self, bar: Bar, now: datetime, pair: str, signals: list[Payload], sold: bool
    ) -> None:
        exits = [s for s in signals if s["kind"] == "exit"]
        if not exits or sold or pair not in self.held:
            return
        live_book = now - bar.close_time <= LIVE_BOOK_WINDOW and not _inside(
            now, self.path.book_outages
        )
        if live_book:
            self._fail(bar, "book", f"exit signalled with a live book at {now} but nothing sold")

    def _mark(self, bar: Bar, pair: str) -> None:
        held = self.held.get(pair)
        if held is not None and bar.close_time > held.marked_at:
            held.mark, held.marked_at = bar.close, bar.close_time
        if held is None or bar.close_time > held.filled_at:
            self._unmarked_since_boot.discard(pair)

    def _check_portfolio(self, bar: Bar, connection: sqlite3.Connection) -> None:
        rows = self._new(
            connection,
            "portfolio_marks",
            "pair_base || '/' || pair_quote, interval, bar_close_time, cash, market_value,"
            " equity, positions_json",
        )
        if not rows:
            self._fail(bar, "equity", "no portfolio mark stored for the bar")
            return
        _, _, _, _, cash_text, value_text, equity_text, positions_text = rows[-1]
        cash, equity = Decimal(cash_text), Decimal(equity_text)
        if cash < 0:
            self._fail(bar, "cash", f"ledger cash is {cash}")
        stored = {
            f"{p['base']}/{p['quote']}": Decimal(p["quantity"]) for p in json.loads(positions_text)
        }
        replayed = {name: h.qty for name, h in self.held.items()}
        if stored != replayed:
            self._fail(bar, "equity", f"ledger holds {stored}, the trades replay to {replayed}")
        marked = sum((h.qty * h.mark for h in self.held.values()), Decimal(0))
        checks = (
            ("cash", cash, self.cash),
            ("equity", equity, self.cash + marked),
            ("cash + value", equity, cash + Decimal(value_text)),
        )
        at_fill = sum(
            (
                h.qty * (h.fill_price if name in self._unmarked_since_boot else h.mark)
                for name, h in self.held.items()
            ),
            Decimal(0),
        )
        for name, got, want in checks:
            if got.quantize(_CENT) == want.quantize(_CENT):
                continue
            if name == "equity" and got.quantize(_CENT) == (self.cash + at_fill).quantize(_CENT):
                self._fail(
                    bar,
                    MARKS_AFTER_BOOT,
                    f"ledger equity {got} values {sorted(self._unmarked_since_boot)} at their "
                    f"fill price after a boot; at the last closes it is {want}",
                )
            else:
                self._fail(bar, "equity", f"ledger {name} {got}, replay {want}")

    def _check_stops(
        self,
        bar: Bar,
        pair: str,
        connection: sqlite3.Connection,
        held_before: set[str],
        signals: list[Payload],
    ) -> None:
        stops = self._new(
            connection, "stop_marks", "pair_base || '/' || pair_quote, bar_close_time, stop_price"
        )
        if bar.interval is not KlineInterval.H4:
            return
        watched = pair in held_before or pair in self.held
        if not watched:
            return
        mine = [Decimal(s[3]) for s in stops if s[1] == pair and _at(s[2]) == bar.close_time]
        if not mine:
            self._fail(bar, "stop", f"{pair} is held and no stop was recorded on this bar")
            return
        stop = mine[-1]
        held = self.held.get(pair)
        if held is None:
            return
        if pair not in held_before and stop >= held.entry_price:
            self._fail(bar, "stop", f"entry stop {stop} not below the entry {held.entry_price}")
        if held.last_stop is not None and stop < held.last_stop:
            self._fail(bar, "stop", f"stop fell from {held.last_stop} to {stop}")
        exits = [s for s in signals if s["kind"] == "exit"]
        if pair in held_before and bar.low <= stop and not exits:
            self._fail(bar, "stop", f"low {bar.low} reached the stop {stop} and no exit fired")
        held.last_stop = stop

    def finish(self, db: Path, exits: list[tuple[int, bool]]) -> None:
        """Check the run as a whole: clean boots, and each bar processed once and in order.

        Args:
            db: The ledger.
            exits: Per boot, its exit code and whether a bar published after
                the next one was due when it stopped.

        """
        tag = f"{self.path.kind} seed {self.path.seed}"
        self.violations.extend(
            f"{tag} boot {number}: serve exited {code}"
            for number, (code, gap_due) in enumerate(exits, start=1)
            if code != 0 and not gap_due
        )
        connection = sqlite3.connect(db)
        try:
            rows = connection.execute(
                "SELECT payload_json FROM events WHERE event_type = 'BarProcessed' ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        handled: dict[tuple[str, str], list[datetime]] = {}
        for (payload,) in rows:
            p = json.loads(payload)
            stream = (f"{p['pair']['base']}/{p['pair']['quote']}", p["interval"])
            handled.setdefault(stream, []).append(datetime.fromisoformat(p["occurred_at"]))
        steps = {"4h": timedelta(hours=4), "1d": timedelta(days=1)}
        self.violations.extend(
            f"{tag}: {name} {interval} handled out of step {wrong[:3]}"
            for (name, interval), closes in sorted(handled.items())
            if (
                wrong := [(a, b) for a, b in itertools.pairwise(closes) if b - a != steps[interval]]
            )
        )
        last_bars = {
            (f"{pair.base.value}/{pair.quote.value}", interval.value): bars[-1].close_time
            for pair in self.path.market.h4
            for interval in (KlineInterval.H4, KlineInterval.D1)
            if (bars := self.path.market.bars(pair, interval))
        }
        self.violations.extend(
            f"{tag}: {name} {interval} last processed {seen}, last bar {close}"
            for (name, interval), close in sorted(last_bars.items())
            if (seen := _last(handled.get((name, interval), []))) != close
        )


def _last(closes: list[datetime]) -> datetime | None:
    return closes[-1] if closes else None


def _inside(now: datetime, windows: list[Window]) -> bool:
    return any(start <= now < end for start, end in windows)


def _at(text: str) -> datetime:
    at = datetime.fromisoformat(text)
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def run_path(
    path: GeneratedPath,
    db: Path,
    *,
    restarts: bool = True,
    after_bar: Callable[[Bar, datetime, sqlite3.Connection], None] | None = None,
) -> list[tuple[int, bool]]:
    """Run serve over ``path`` on ``db``, booting it again at each restart.

    A boot that exits 1 while a bar published after the next one is still due
    is booted again one minute after that bar is out, as systemd restarts serve
    (the boots in between would stop on the same hole; issue #852 is the bar
    that never comes).

    Args:
        path: The generated path.
        db: Ledger file, shared by every boot.
        restarts: Boot again at ``path.restarts``; ``False`` runs straight through.
        after_bar: Called after each handled bar (the oracle).

    Returns:
        Per boot, its exit code and whether a late bar was due when it stopped.

    """
    stops = [*path.restarts, path.end] if restarts else [path.end]
    # Captured, not rendered: the console renderer prints a stopped boot's traceback with
    # every frame's locals, which took 15 s of a 20 s run.
    with capture_logs():
        return _boots(path, db, stops, after_bar)


def _boots(
    path: GeneratedPath,
    db: Path,
    stops: list[datetime],
    after_bar: Callable[[Bar, datetime, sqlite3.Connection], None] | None,
) -> list[tuple[int, bool]]:
    boot = path.live_from
    exits: list[tuple[int, bool]] = []
    for until in stops:
        while boot < until:
            served = harness.serve(
                path.market,
                db,
                live_from=boot,
                until=until,
                book_outages=path.book_outages,
                late_bars=path.late_bars,
                klines_outages=path.klines_outages,
                fx_outages=path.fx_outages,
                duplicate_bars=path.duplicate_bars,
                after_bar=after_bar,
            )
            stopped = served.clock.now()
            due = [
                shown
                for _, interval, open_time, shown in path.late_bars
                if open_time + _duration(interval) <= stopped < shown
            ]
            exits.append((served.exit_code, bool(due)))
            if served.exit_code == 0:
                # The simulated clock stops on the feed's first sleep past ``until``, which
                # can be the next bar's delivery: the next boot starts there, never earlier.
                boot = max(until, stopped)
            elif due:
                boot = max(due) + timedelta(minutes=1)
            else:
                return exits
        boot = max(boot, until)
    return exits


def _duration(interval: KlineInterval) -> timedelta:
    return timedelta(hours=4) if interval is KlineInterval.H4 else timedelta(days=1)


def fingerprint(db: Path) -> dict[str, list[tuple[str, ...]]]:
    """Return what a run decided, for comparing a restarted run with a straight one.

    Signals, trades (with their times, quantities, prices and fees) and stops
    must match when the only difference is a restart. The portfolio marks are
    left out: the oracle already holds each one to the replay of the trades,
    which a restart does not change.
    """
    connection = sqlite3.connect(db)
    try:
        queries = {
            "signals": "SELECT payload_json FROM events WHERE event_type = 'SignalEmitted'"
            " ORDER BY id",
            "trades": "SELECT pair_base, side, qty_amount, price_amount, fee_amount, executed_at"
            " FROM trades ORDER BY id",
            "stops": "SELECT pair_base, bar_close_time, stop_price FROM stop_marks"
            " ORDER BY bar_close_time, pair_base",
        }
        result = {
            name: [tuple(str(v) for v in row) for row in connection.execute(sql).fetchall()]
            for name, sql in queries.items()
        }
    finally:
        connection.close()
    result["signals"] = [_signal_key(row[0]) for row in result["signals"]]
    return result


def _signal_key(payload: str) -> tuple[str, ...]:
    p = json.loads(payload)
    return (
        p["pair"]["base"],
        p["kind"],
        str(p["exit_reason"]),
        p["occurred_at"],
        p["entry_price"],
        p["stop_price"],
    )


def differences(
    restarted: dict[str, list[tuple[str, ...]]], straight: dict[str, list[tuple[str, ...]]]
) -> list[str]:
    """Return the first difference in each table between two fingerprints."""
    return [
        _first_difference(table, restarted.get(table, []), straight[table])
        for table in sorted(straight)
        if restarted.get(table, []) != straight[table]
    ]


def _first_difference(table: str, a: list[tuple[str, ...]], b: list[tuple[str, ...]]) -> str:
    first = next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b))
    )
    left = a[first] if first < len(a) else None
    right = b[first] if first < len(b) else None
    return (
        f"{table}: {len(a)} rows restarted, {len(b)} straight; "
        f"first difference at row {first}: {left} vs {right}"
    )
