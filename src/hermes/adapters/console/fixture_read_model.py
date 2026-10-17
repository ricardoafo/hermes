"""Console read model served from named JSON scenarios.

The scenarios ship inside the package (``scenarios/*.json``) so
``hermes --demo <scenario>`` works from an installed wheel, and the tests load
the same files. Every amount is a JSON string parsed into an exact ``Decimal``;
every timestamp must carry an offset. Figures the views can derive (row net,
change since entry, stop distance) are computed here rather than stored, so a
scenario cannot contradict itself.

Reference: openspec/changes/console-read-model/change.md (D-3, D-7).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Final, cast

from hermes.adapters.console.shadow_records import ShadowRecordError, parse_run_record
from hermes.application.console.portfolio_figures import range_start
from hermes.application.console.render import MADRID
from hermes.application.console.shadow_screen import (
    NotebookEntry,
    ShadowEvidence,
    ShadowRunRecord,
)
from hermes.application.console.views import (
    QUIET_ACTIVITY_KINDS,
    ActivityDay,
    ActivityItem,
    ActivityKind,
    ConsoleBrief,
    Heartbeat,
    HeartbeatState,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    RuleCheck,
    ShadowTest,
    ShadowTestStatus,
    Tax,
    TaxBand,
    TaxEstimate,
    TaxPending,
    TimelineEvent,
    WatchedPair,
    WaterfallColumn,
    tax_amount,
)
from hermes.application.supervisor.briefing import BriefKind, publishable

__all__ = [
    "FixtureReadModel",
    "ScenarioError",
    "UnknownScenarioError",
    "available_scenarios",
    "desk_inputs",
]

_SCENARIO_PACKAGE: Final = "hermes.adapters.console"
_SCENARIO_DIR: Final = "scenarios"
_SUFFIX: Final = ".json"
_HUNDRED: Final = Decimal(100)
_SIGNAL_KINDS: Final = frozenset({"entry", "exit"})

type _Json = Mapping[str, object]


class ScenarioError(ValueError):
    """A scenario file is missing a field or holds an invalid value."""


class UnknownScenarioError(ScenarioError):
    """No scenario has the requested name."""

    def __init__(self, name: str, available: Sequence[str]) -> None:
        """Name the missing scenario and list the valid ones."""
        super().__init__(f"unknown demo scenario {name!r}; available: {', '.join(available)}")
        self.name = name
        self.available = tuple(available)


def available_scenarios() -> tuple[str, ...]:
    """Return the names of the packaged scenarios, sorted."""
    folder = resources.files(_SCENARIO_PACKAGE) / _SCENARIO_DIR
    return tuple(
        sorted(
            entry.name.removesuffix(_SUFFIX)
            for entry in folder.iterdir()
            if entry.name.endswith(_SUFFIX)
        )
    )


# ----------------------------------------------------------------------
# Field readers
# ----------------------------------------------------------------------


def _field(obj: _Json, key: str) -> object:
    if key not in obj:
        msg = f"missing field {key!r}"
        raise ScenarioError(msg)
    return obj[key]


def _obj(obj: _Json, key: str) -> _Json:
    value = _field(obj, key)
    if not isinstance(value, Mapping):
        msg = f"field {key!r} must be an object"
        raise ScenarioError(msg)
    return cast("_Json", value)


def _list(obj: _Json, key: str) -> list[object]:
    value = obj.get(key, [])
    if not isinstance(value, list):
        msg = f"field {key!r} must be a list"
        raise ScenarioError(msg)
    return cast("list[object]", value)


def _str(obj: _Json, key: str) -> str:
    value = _field(obj, key)
    if not isinstance(value, str):
        msg = f"field {key!r} must be a string"
        raise ScenarioError(msg)
    return value


def _opt_str(obj: _Json, key: str) -> str | None:
    return None if obj.get(key) is None else _str(obj, key)


def _int(obj: _Json, key: str) -> int:
    value = _field(obj, key)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"field {key!r} must be an integer"
        raise ScenarioError(msg)
    return value


def _to_decimal(raw: object, key: str) -> Decimal:
    if not isinstance(raw, str):
        msg = f"field {key!r} must be a decimal string, got {raw!r}"
        raise ScenarioError(msg)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        msg = f"field {key!r} is not a decimal: {raw!r}"
        raise ScenarioError(msg) from exc
    if not value.is_finite():
        msg = f"field {key!r} must be finite: {raw!r}"
        raise ScenarioError(msg)
    return value


def _dec(obj: _Json, key: str) -> Decimal:
    return _to_decimal(_field(obj, key), key)


def _opt_dec(obj: _Json, key: str) -> Decimal | None:
    return None if obj.get(key) is None else _dec(obj, key)


def _dec_list(obj: _Json, key: str) -> tuple[Decimal, ...]:
    return tuple(_to_decimal(raw, key) for raw in _list(obj, key))


def _to_datetime(raw: object, key: str) -> datetime:
    if not isinstance(raw, str):
        msg = f"field {key!r} must be an ISO timestamp string"
        raise ScenarioError(msg)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        msg = f"field {key!r} is not an ISO timestamp: {raw!r}"
        raise ScenarioError(msg) from exc
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"field {key!r} has no timezone offset: {raw!r}"
        raise ScenarioError(msg)
    return value


def _dt(obj: _Json, key: str) -> datetime:
    return _to_datetime(_field(obj, key), key)


def _opt_dt(obj: _Json, key: str) -> datetime | None:
    return None if obj.get(key) is None else _dt(obj, key)


def _items(obj: _Json, key: str) -> list[_Json]:
    out: list[_Json] = []
    for entry in _list(obj, key):
        if not isinstance(entry, Mapping):
            msg = f"every entry of {key!r} must be an object"
            raise ScenarioError(msg)
        out.append(cast("_Json", entry))
    return out


def _enum[E: (HeartbeatState, ActivityKind, ShadowTestStatus, PortfolioRange)](
    kind: type[E], raw: str, key: str
) -> E:
    try:
        return kind(raw)
    except ValueError as exc:
        msg = f"field {key!r} has unknown value {raw!r}"
        raise ScenarioError(msg) from exc


# ----------------------------------------------------------------------
# View parsers
# ----------------------------------------------------------------------


def _tax(obj: _Json, key: str) -> Tax:
    raw = _obj(obj, key)
    if raw.get("pending") is True:
        return TaxPending()
    return TaxEstimate(amount=_dec(raw, "estimate"))


def _heartbeat(raw: _Json) -> Heartbeat:
    try:
        return Heartbeat(
            state=_enum(HeartbeatState, _str(raw, "state"), "state"),
            as_of=_dt(raw, "as_of"),
            last_check_at=_dt(raw, "last_check_at"),
            next_check_at=_dt(raw, "next_check_at"),
            down_since=_opt_dt(raw, "down_since"),
        )
    except ValueError as exc:
        if isinstance(exc, ScenarioError):
            raise
        raise ScenarioError(str(exc)) from exc


def _waterfall(raw: _Json) -> WaterfallColumn:
    return WaterfallColumn(gross=_dec(raw, "gross"), fees=_dec(raw, "fees"), tax=_tax(raw, "tax"))


def _tax_band(raw: _Json) -> TaxBand | None:
    band = raw.get("tax_band")
    if band is None:
        return None
    if not isinstance(band, Mapping):
        msg = "field 'tax_band' must be an object or null"
        raise ScenarioError(msg)
    band_obj = cast("_Json", band)
    return TaxBand(
        rate=_dec(band_obj, "rate"),
        next_rate=_opt_dec(band_obj, "next_rate"),
        room_to_next=_opt_dec(band_obj, "room_to_next"),
    )


def _portfolios(raw: _Json) -> dict[PortfolioRange, PortfolioSummary]:
    ranges = _obj(raw, "ranges")
    as_of = _dt(raw, "as_of")
    total_value = _dec(raw, "total_value")
    cash = _dec(raw, "cash")
    invested = _dec(raw, "invested")
    today = _waterfall(_obj(raw, "today"))
    year = _waterfall(_obj(raw, "year"))
    open_pnl = _dec(raw, "open_pnl")
    open_tax = _tax(raw, "open_tax_if_sold")
    band = _tax_band(raw)
    started_on = _opt_str(raw, "started_on")
    out: dict[PortfolioRange, PortfolioSummary] = {}
    for range_ in PortfolioRange:
        entry = _obj(ranges, range_.value)
        out[range_] = PortfolioSummary(
            range=range_,
            as_of=as_of,
            total_value=total_value,
            change=_dec(entry, "change"),
            change_pct=_dec(entry, "change_pct"),
            net=_dec(entry, "net"),
            sparkline=_dec_list(entry, "sparkline"),
            cash=cash,
            invested=invested,
            today=today,
            year=year,
            open_pnl=open_pnl,
            open_tax_if_sold=open_tax,
            tax_band=band,
            starting_capital=_opt_dec(raw, "starting_capital"),
            started_on=_to_date(started_on, "started_on") if started_on else None,
            range_from=range_start(range_, as_of, MADRID),
        )
    return out


def _to_date(raw: str, key: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        msg = f"field {key!r} is not an ISO date: {raw!r}"
        raise ScenarioError(msg) from exc


def _position(raw: _Json, rate: Decimal) -> PositionDetail:
    detail = _obj(raw, "detail")
    entry = _dec(raw, "entry_price")
    current = _dec(raw, "current_price")
    stop = _dec(raw, "stop_price")
    atr = _dec(raw, "atr")
    if entry <= 0 or current <= 0 or atr <= 0:
        msg = f"position {raw.get('coin')!r}: prices and ATR must be positive"
        raise ScenarioError(msg)
    tax = _tax(detail, "tax")
    gross = _dec(detail, "gross")
    fees_paid = _dec(detail, "fees_paid")
    fees_to_close = _dec(detail, "fees_to_close")
    row = PositionRow(
        coin=_str(raw, "coin"),
        timeframe=_str(raw, "timeframe"),
        side=_str(raw, "side"),
        size=_dec(raw, "size"),
        opened_at=_dt(raw, "opened_at"),
        entry_price=entry,
        current_price=current,
        change_pct=(current - entry) * _HUNDRED / entry,
        net=gross - fees_paid - fees_to_close - tax_amount(tax),
        stop_price=stop,
        high_since_entry=_dec(raw, "high_since_entry"),
        stop_distance_atr=(current - stop) / atr,
        stop_distance_pct=(current - stop) * _HUNDRED / current,
        eur_per_usdt=rate,
    )
    entry_values: list[tuple[str, Decimal]] = []
    for pair in _list(detail, "entry_values"):
        if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)):  # noqa: PLR2004
            msg = "entry_values entries must be [name, value] pairs"
            raise ScenarioError(msg)
        entry_values.append((pair[0], _to_decimal(pair[1], "entry_values")))
    return PositionDetail(
        row=row,
        strategy=_str(detail, "strategy"),
        entry_values=tuple(entry_values),
        stop_moves=_int(detail, "stop_moves"),
        gross=gross,
        fees_paid=fees_paid,
        fees_to_close=fees_to_close,
        tax=tax,
        price_path=_dec_list(detail, "price_path"),
        timeline=tuple(
            TimelineEvent(at=_dt(event, "at"), label=_str(event, "label"))
            for event in _items(detail, "timeline")
        ),
    )


def _activity_item(raw: _Json, rate: Decimal) -> ActivityItem:
    return ActivityItem(
        at=_dt(raw, "at"),
        kind=_enum(ActivityKind, _str(raw, "kind"), "kind"),
        coin=_opt_str(raw, "coin"),
        text=_opt_str(raw, "text"),
        price=_opt_dec(raw, "price"),
        net=_opt_dec(raw, "net"),
        count=_int(raw, "count") if "count" in raw else 1,
        stop_from=_opt_dec(raw, "stop_from"),
        stop_to=_opt_dec(raw, "stop_to"),
        eur_per_usdt=rate,
    )


def _activity(raw: _Json, rate: Decimal) -> tuple[ActivityDay, ...]:
    days: list[ActivityDay] = []
    for day in _items(raw, "activity"):
        try:
            day_value = date.fromisoformat(_str(day, "day"))
        except ValueError as exc:
            msg = f"activity day is not an ISO date: {day.get('day')!r}"
            raise ScenarioError(msg) from exc
        days.append(
            ActivityDay(
                day=day_value,
                items=tuple(_activity_item(item, rate) for item in _items(day, "items")),
            )
        )
    return tuple(days)


# A scenario stores no rule checks, so each 4h stream gets a fixed, varied
# pattern over the last week (#714). Breakout and momentum never hold together,
# so the only signal is the bar a held coin opened on: all three rules met and
# no entry check after it, as serve logs none while a position is open.
_CHECK_STEP: Final = timedelta(hours=4)
_CHECK_WINDOW: Final = timedelta(days=7)
# The second coin's trend rule is off for 3 of every 12 checks (12 hours every two days).
_TREND_OFF_FROM: Final = 9


def _rule_checks(
    watched: Sequence[WatchedPair], rows: Sequence[PositionRow]
) -> tuple[RuleCheck, ...]:
    opened = {row.coin.upper(): row.opened_at for row in rows if row.timeframe == "4h"}
    checks: list[RuleCheck] = []
    for lane, pair in enumerate(sorted(watched, key=lambda w: w.coin)):
        if pair.timeframe != "4h":
            continue
        entry = opened.get(pair.coin.upper())
        at = pair.last_check_at if entry is None else entry
        step = 0
        while pair.last_check_at - at <= _CHECK_WINDOW:
            signal_bar = entry is not None and step == 0
            breakout = (step + 2 * lane) % 7 == 0
            checks.append(
                RuleCheck(
                    at=at,
                    coin=pair.coin,
                    interval=pair.timeframe,
                    breakout=signal_bar or breakout,
                    momentum=signal_bar or (not breakout and (step + lane) % 3 != 0),
                    regime=signal_bar or lane != 1 or step % 12 < _TREND_OFF_FROM,
                )
            )
            at -= _CHECK_STEP
            step += 1
    return tuple(sorted(checks, key=lambda c: (-c.at.timestamp(), c.coin)))


def _shadow_test(raw: _Json) -> ShadowTest:
    return ShadowTest(
        status=_enum(ShadowTestStatus, _str(raw, "status"), "status"),
        day=None if raw.get("day") is None else _int(raw, "day"),
        length_days=_int(raw, "length_days") if "length_days" in raw else 90,
        note=_opt_str(raw, "note"),
    )


def _opt_bool(obj: _Json, key: str) -> bool | None:
    value = obj.get(key)
    if value is not None and not isinstance(value, bool):
        msg = f"field {key!r} must be true, false or null"
        raise ScenarioError(msg)
    return value


def _notebook(raw: _Json) -> tuple[NotebookEntry, ...] | None:
    if raw.get("notebook") is None:
        return None
    return tuple(
        NotebookEntry(
            at=_dt(entry, "at"),
            phase=_str(entry, "phase"),
            actor=_str(entry, "actor"),
            action=_str(entry, "action"),
        )
        for entry in _items(raw, "notebook")
    )


def _shadow_run(raw: _Json) -> ShadowRunRecord | None:
    if raw.get("run") is None:
        return None
    try:
        return parse_run_record(_obj(raw, "run"))
    except ShadowRecordError as exc:
        msg = f"shadow_screen.run: {exc}"
        raise ScenarioError(msg) from exc


def _shadow_evidence(raw: _Json) -> ShadowEvidence:
    kill_fired = _opt_bool(raw, "kill_fired")
    return ShadowEvidence(
        seeded_at=_opt_dt(raw, "seeded_at"),
        first_fill_at=_opt_dt(raw, "first_fill_at"),
        missed_days=tuple(
            _to_date(_to_str(day, "missed_days"), "missed_days")
            for day in _list(raw, "missed_days")
        ),
        live_drifts_bps=_dec_list(raw, "live_drifts_bps"),
        kill_fired=bool(kill_fired),
        kill_check_clean=_opt_bool(raw, "kill_check_clean"),
    )


def _to_str(raw: object, key: str) -> str:
    if not isinstance(raw, str):
        msg = f"every entry of {key!r} must be a string"
        raise ScenarioError(msg)
    return raw


def _watched(raw: _Json) -> WatchedPair:
    signal = _opt_str(raw, "signal")
    if signal is not None and signal not in _SIGNAL_KINDS:
        msg = f"field 'signal' has unknown value {signal!r}"
        raise ScenarioError(msg)
    last = _dt(raw, "last_check_at")
    following = _dt(raw, "next_check_at")
    if following <= last:
        msg = f"market watch {raw.get('coin')!r}: next_check_at must follow last_check_at"
        raise ScenarioError(msg)
    return WatchedPair(
        coin=_str(raw, "coin"),
        quote=_str(raw, "quote"),
        timeframe=_str(raw, "timeframe"),
        last_check_at=last,
        signal=signal,
        next_check_at=following,
    )


# A scenario holds no supervisor tool payloads, so these are the payloads the
# desk's tools would return for it. Like the real tools, they carry no price,
# value or P&L; the real desk then composes and gates the brief (#720).
_BARS_PER_DAY: Final = {"4h": 6, "1d": 1}
_DESK_HEADLINES: Final = {
    HeartbeatState.OK: "every stream is on time",
    HeartbeatState.LATE: "a bar check is late",
    HeartbeatState.DOWN: "hermes is not running",
}


def _engine_row(pair: WatchedPair, held: Mapping[str, str]) -> dict[str, object]:
    entries = 1 if pair.signal == "entry" else 0
    exits = ["trailing stop"] if pair.signal == "exit" else []
    return {
        "pair": f"{pair.coin}/{pair.quote}",
        "bars": [{"interval": pair.timeframe, "count": _BARS_PER_DAY.get(pair.timeframe, 1)}],
        "entries": entries,
        "exit_reasons": exits,
        "fills_buy": entries,
        "fills_sell": len(exits),
        "holding_since": held.get(pair.coin.upper()),
    }


def desk_inputs(
    heartbeat: Heartbeat, positions: Sequence[PositionRow], watched: Sequence[WatchedPair]
) -> dict[str, object]:
    """Return what the supervisor desk's tools would return for a scenario (#720).

    Args:
        heartbeat: The scenario heartbeat; its read time is the brief's.
        positions: The open positions; only their pair and entry time are used.
        watched: The checked streams.

    Returns:
        JSON-ready input in the shape
        :func:`~hermes.application.supervisor.briefing.gather` returns.
    """
    now = heartbeat.as_of
    held = {row.coin.upper(): row.opened_at.isoformat() for row in positions}
    signals = [
        {
            "pair": f"{w.coin}/{w.quote}",
            "kind": w.signal,
            "occurred_at": w.last_check_at.isoformat(),
        }
        for w in watched
        if w.signal is not None
    ]
    open_rows = [
        {"pair": f"{row.coin}/USDT", "entry_time": row.opened_at.isoformat()} for row in positions
    ]
    return {
        "now": now.isoformat(),
        "since": (now - timedelta(days=1)).isoformat(),
        "health": {
            "verdict": heartbeat.state.value,
            "headline": _DESK_HEADLINES[heartbeat.state],
            "streams": [],
        },
        "signals": {"signals": signals},
        "engine": {"pairs": [_engine_row(w, held) for w in watched]},
        "positions": {"positions": open_rows},
        "decisions": {"reviewable": [], "sealed": []},
        "counts": {
            "signals": len(signals),
            "entries": sum(1 for s in signals if s["kind"] == "entry"),
            "exits": sum(1 for s in signals if s["kind"] == "exit"),
            "open_positions": len(open_rows),
            "decisions_waiting": 0,
            "decisions_sealed": 0,
        },
    }


# ----------------------------------------------------------------------
# Read model
# ----------------------------------------------------------------------


class FixtureReadModel:
    """A :class:`~hermes.application.console.read_model.ConsoleReadModel` over one scenario.

    Construction parses and validates the whole scenario, so a broken fixture
    fails on load rather than halfway through a render.
    """

    def __init__(self, scenario: str, document: _Json) -> None:
        """Parse ``document`` as the scenario named ``scenario``.

        Args:
            scenario: Name shown in the DEMO DATA tag.
            document: The decoded scenario JSON.

        Raises:
            ScenarioError: A field is missing or invalid.
        """
        self._scenario = scenario
        self._heartbeat = _heartbeat(_obj(document, "heartbeat"))
        self._portfolios = _portfolios(_obj(document, "portfolio"))
        rate = _dec(document, "eur_per_usdt")
        if rate <= 0:
            msg = "field 'eur_per_usdt' must be positive"
            raise ScenarioError(msg)
        details = tuple(_position(raw, rate) for raw in _items(document, "positions"))
        self._details = {detail.row.coin.upper(): detail for detail in details}
        self._rows = tuple(detail.row for detail in details)
        self._activity = _activity(document, rate)
        self._shadow_test = _shadow_test(_obj(document, "shadow_test"))
        self._market_watch = tuple(_watched(raw) for raw in _items(document, "market_watch"))
        shadow = _obj(document, "shadow_screen")
        self._shadow_run = _shadow_run(shadow)
        self._shadow_evidence = _shadow_evidence(shadow)
        self._notebook = _notebook(shadow)
        self._rule_checks = _rule_checks(self._market_watch, self._rows)

    @classmethod
    def load(cls, name: str) -> FixtureReadModel:
        """Load a packaged scenario by name.

        Args:
            name: Scenario name, e.g. ``"ok-two-positions"``.

        Returns:
            The read model.

        Raises:
            UnknownScenarioError: No packaged scenario has that name.
            ScenarioError: The scenario file is invalid.
        """
        available = available_scenarios()
        if name not in available:
            raise UnknownScenarioError(name, available)
        resource = resources.files(_SCENARIO_PACKAGE) / _SCENARIO_DIR / f"{name}{_SUFFIX}"
        return cls(name, _decode(resource.read_text(encoding="utf-8")))

    @classmethod
    def from_path(cls, path: Path) -> FixtureReadModel:
        """Load a scenario from a file; its stem becomes the scenario name.

        Args:
            path: JSON scenario file.

        Returns:
            The read model.

        Raises:
            ScenarioError: The file is not a valid scenario.
        """
        return cls(path.stem, _decode(path.read_text(encoding="utf-8")))

    @property
    def demo_scenario(self) -> str:
        """Return the scenario name; fixture data is always demo data."""
        return self._scenario

    def heartbeat(self) -> Heartbeat:
        """Return the scenario heartbeat."""
        return self._heartbeat

    def portfolio(self, range_: PortfolioRange = PortfolioRange.TODAY) -> PortfolioSummary:
        """Return the scenario portfolio for ``range_``."""
        return self._portfolios[range_]

    def positions(self) -> tuple[PositionRow, ...]:
        """Return the scenario's open positions in file order."""
        return self._rows

    def position(self, coin: str) -> PositionDetail | None:
        """Return the detail for ``coin`` (case-insensitive), or ``None``."""
        return self._details.get(coin.upper())

    def activity(self, *, include_quiet: bool = False) -> tuple[ActivityDay, ...]:
        """Return activity days; bar checks and signals only when asked."""
        if include_quiet:
            return self._activity
        days = (
            ActivityDay(
                day=day.day,
                items=tuple(i for i in day.items if i.kind not in QUIET_ACTIVITY_KINDS),
            )
            for day in self._activity
        )
        return tuple(day for day in days if day.items)

    def rule_checks(self) -> tuple[RuleCheck, ...]:
        """Return the scenario's yes/no entry rules per 4h check, newest first (#714)."""
        return self._rule_checks

    def shadow_test(self) -> ShadowTest:
        """Return the scenario's shadow test status."""
        return self._shadow_test

    def market_watch(self) -> tuple[WatchedPair, ...]:
        """Return the scenario's checked streams in file order (no prices, #650)."""
        return self._market_watch

    def shadow_evidence(self, since: datetime | None) -> ShadowEvidence:
        """Return the scenario's shadow facts; the seed and first fill alone before day 0."""
        evidence = self._shadow_evidence
        if since is None:
            return ShadowEvidence(
                seeded_at=evidence.seeded_at, first_fill_at=evidence.first_fill_at
            )
        return evidence

    def latest_run(self, strategy_id: str) -> ShadowRunRecord | None:
        """Return the scenario's day-0 record; a scenario holds one strategy."""
        del strategy_id
        return self._shadow_run

    def notebook_entries(self) -> tuple[NotebookEntry, ...] | None:
        """Return the scenario's synthetic lab notebook entries, oldest first."""
        return self._notebook

    def briefs(self) -> tuple[ConsoleBrief, ...]:
        """Return the scenario's daily brief, composed and gated by the supervisor desk (#720)."""
        inputs = desk_inputs(self._heartbeat, self._rows, self._market_watch)
        brief, verdict = publishable(inputs, kind=BriefKind.DAILY)
        return (ConsoleBrief.published(brief, verdict, as_of=self._heartbeat.as_of),)


def _decode(text: str) -> _Json:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"scenario is not valid JSON: {exc}"
        raise ScenarioError(msg) from exc
    if not isinstance(document, dict):
        msg = "scenario must be a JSON object"
        raise ScenarioError(msg)
    return cast("_Json", document)
