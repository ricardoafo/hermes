"""Unit tests for scripts/backtest/decision_replay.py (#798, ADR-025).

Invariants (798-train-decision-replay change.md R-1 to R-5):

- The recording kernel decides exactly as the production kernel does and
  keeps one decision per signal it executes, with the refusing rule.
- A file written by ``record`` reads back as the same decisions.
- ``diff`` reports a changed decision when a signal exists on one side only
  or its outcome differs; the refusing rule alone is never a change.
- ``diff`` exits 0 with no change, 1 with any change, 3 on an unreadable file.
- The window is the manifest's Train split, whole days, as the backtest
  workflow derives it.
- A record file carries no price, size, equity or metric.
"""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from hermes.application.backtest.replay import replay
from hermes.application.execution.kernel import Declined, Executed, Halted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide, StrategySignal
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from tests.support.fill_kernel import BTC_USDT, SIZING, STRATEGY, fees, kernel, market

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _ROOT / "scripts" / "backtest"
_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _import_script() -> ModuleType:
    """Import the script; it imports its sibling modules by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "decision_replay_script", _SCRIPTS / "decision_replay.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["decision_replay_script"] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _import_script()
Decision = SCRIPT.Decision


def _decision(hours: int, *, executed: bool = True, kind: str = "entry", rule: str = "") -> object:
    return Decision(
        signal_time=(_T0 + timedelta(hours=hours)).isoformat(),
        pair="BTC/USDT",
        kind=kind,
        side="long",
        executed=executed,
        rule=rule,
    )


def _bar(close_time: datetime, close: Decimal) -> Bar:
    return Bar(
        pair=BTC_USDT,
        interval=KlineInterval.H4,
        open_time=close_time - timedelta(hours=4),
        open=close,
        high=close * Decimal("1.01"),
        low=close * Decimal("0.99"),
        close=close,
        volume=Decimal("1000"),
        close_time=close_time,
    )


def _entry(at: datetime, price: Decimal) -> StrategySignal:
    return StrategySignal(
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=price,
        stop_price=price * Decimal("0.9"),
        atr_context=Decimal("2"),
        signal_time=at,
    )


class _OneSignal:
    """Emits ``signal`` on the bar closing at its signal time."""

    def __init__(self, signal: StrategySignal) -> None:
        self.signal = signal

    def on_bar(self, bar: Bar, portfolio_state: object) -> StrategySignal | None:
        _ = portfolio_state
        return self.signal if bar.close_time == self.signal.signal_time else None

    def seed(self, bars: object, open_position: object = None) -> None:
        _ = (bars, open_position)

    def restore_position(self, open_position: object) -> None:
        _ = open_position


def _recording_kernel() -> object:
    return SCRIPT._kernel(
        STRATEGY, Money(Decimal("5000"), Currency.USDT), SIZING, kernel_type=SCRIPT.RecordingKernel
    )


class TestRecordingKernel:
    """The recording kernel decides as the production kernel and keeps each decision."""

    def test_an_executed_entry_is_recorded_as_executed(self) -> None:
        """A replayed entry that fills is one executed decision at its signal time."""
        at = _T0 + timedelta(hours=4)
        recording = _recording_kernel()
        run = replay(
            [_bar(at, Decimal("100"))],
            strategy=_OneSignal(_entry(at, Decimal("100"))),
            kernel=recording,
            window_start=_T0,
            window_end=_T0 + timedelta(days=1),
            fees_at=lambda _at, pair: fees(pair),
            correlation_id=lambda: "c1",
        )
        assert len(run.fills) == 1
        assert recording.decisions == [
            Decision(
                signal_time=at.isoformat(),
                pair="BTC/USDT",
                kind="entry",
                side="long",
                executed=True,
                rule="",
            )
        ]

    def test_a_declined_signal_keeps_the_rule_that_refused_it(self) -> None:
        """No book: the production kernel declines as ``no-book`` and so does the record."""
        recording = _recording_kernel()
        outcome = recording.execute(
            _entry(_T0, Decimal("100")), BTC_USDT, market(None, _T0), correlation_id="c1"
        )
        assert isinstance(outcome, Declined)
        assert (recording.decisions[0].executed, recording.decisions[0].rule) == (False, "no-book")

    def test_outcome_is_the_production_kernels(self) -> None:
        """The recording kernel returns what an unwrapped kernel returns for the same call."""
        signal = _entry(_T0, Decimal("100"))
        expected = Declined(reason=f"no order book for {BTC_USDT} at {_T0}", rule="no-book")
        plain = kernel().execute(signal, BTC_USDT, market(None, _T0), correlation_id="c1")
        recorded = _recording_kernel().execute(
            signal, BTC_USDT, market(None, _T0), correlation_id="c1"
        )
        assert (plain, recorded) == (expected, expected)


class TestRefusingRule:
    """What a decision names as the reason a signal did not fill."""

    @pytest.mark.parametrize(
        ("outcome", "rule"),
        [
            (Declined(reason="RO-04: book stale", rule="HALT"), "HALT:RO-04"),
            (Declined(reason="no rule id", rule="HALT"), "HALT:unknown"),
            (Declined(reason="cap", rule="sizing"), "sizing"),
        ],
    )
    def test_declined_names_its_rule(self, outcome: Declined, rule: str) -> None:
        """A declined signal names the router rule, or HALT with its RO id."""
        assert SCRIPT.refusing_rule(outcome) == rule

    def test_halted_is_the_kill(self) -> None:
        """A §10 halt is recorded as ``kill``."""
        assert SCRIPT.refusing_rule(Halted(breaker=object())) == "kill"

    def test_executed_has_no_rule(self) -> None:
        """A fill has no refusing rule."""
        assert SCRIPT.refusing_rule(Executed(fill=object(), trade=object())) == ""


class TestFile:
    """The record file round trip and its fields."""

    def test_record_file_reads_back_the_same_decisions(self, tmp_path: Path) -> None:
        """Writing then reading is the identity on a list of decisions, order kept."""
        decisions = [_decision(4), _decision(8, executed=False, kind="exit", rule="no-position")]
        path = tmp_path / "base.jsonl"
        SCRIPT.write_decisions(path, decisions)
        assert SCRIPT.read_decisions(path) == decisions

    def test_record_file_holds_decision_fields_only(self, tmp_path: Path) -> None:
        """Each line has exactly the decision fields: no price, size, equity or metric."""
        path = tmp_path / "base.jsonl"
        SCRIPT.write_decisions(path, [_decision(4)])
        row = json.loads(path.read_text(encoding="utf-8"))
        assert sorted(row) == ["executed", "kind", "pair", "rule", "side", "signal_time"]

    def test_a_line_that_is_not_a_decision_is_refused(self, tmp_path: Path) -> None:
        """A malformed line raises ValueError naming the file and line."""
        path = tmp_path / "bad.jsonl"
        path.write_text('{"pair": "BTC/USDT"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match=r"bad\.jsonl:1"):
            SCRIPT.read_decisions(path)


class TestChangedDecisions:
    """What counts as a changed decision between base and head."""

    def test_same_decisions_change_nothing(self) -> None:
        """Identical files: no changed decision."""
        both = [_decision(4), _decision(8, kind="exit")]
        assert SCRIPT.changed_decisions(both, list(both)) == []

    def test_a_flipped_outcome_is_a_change(self) -> None:
        """Executed on base, declined on head: one change naming both outcomes."""
        lines = SCRIPT.changed_decisions(
            [_decision(4)], [_decision(4, executed=False, rule="sizing")]
        )
        assert lines == [
            f"CHANGED {(_T0 + timedelta(hours=4)).isoformat()} BTC/USDT: "
            "base entry long executed; head entry long not executed (sizing)"
        ]

    def test_a_signal_on_one_side_only_is_a_change(self) -> None:
        """A signal the base never produced is a change from ``no signal``."""
        lines = SCRIPT.changed_decisions([], [_decision(4)])
        assert lines == [
            f"CHANGED {(_T0 + timedelta(hours=4)).isoformat()} BTC/USDT: "
            "base no signal; head entry long executed"
        ]

    def test_a_different_rule_alone_is_no_change(self) -> None:
        """Both declined, by different rules: the decision is the same."""
        base = [_decision(4, executed=False, rule="sizing")]
        head = [_decision(4, executed=False, rule="HALT:RO-01")]
        assert SCRIPT.changed_decisions(base, head) == []

    def test_changes_are_listed_oldest_first(self) -> None:
        """Changes come out in signal-time order whatever the file order."""
        lines = SCRIPT.changed_decisions([], [_decision(8), _decision(4)])
        assert [line.split()[1] for line in lines] == [
            (_T0 + timedelta(hours=4)).isoformat(),
            (_T0 + timedelta(hours=8)).isoformat(),
        ]


class TestDiffExitCodes:
    """``diff`` exit codes."""

    def _files(self, tmp_path: Path, base: list[object], head: list[object]) -> list[str]:
        SCRIPT.write_decisions(tmp_path / "base.jsonl", base)
        SCRIPT.write_decisions(tmp_path / "head.jsonl", head)
        return ["diff", str(tmp_path / "base.jsonl"), str(tmp_path / "head.jsonl")]

    def test_no_change_exits_0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Matching files exit 0 and say how many decisions matched."""
        assert SCRIPT.main(self._files(tmp_path, [_decision(4)], [_decision(4)])) == 0
        assert "OK: all 1 Train decisions match the base" in capsys.readouterr().out

    def test_any_change_exits_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """One changed decision exits 1 and prints the count."""
        assert SCRIPT.main(self._files(tmp_path, [_decision(4)], [])) == 1
        assert "1 of 1 base decisions changed on Train" in capsys.readouterr().out

    def test_unreadable_file_exits_3(self, tmp_path: Path) -> None:
        """A missing file exits 3, never 0."""
        missing = str(tmp_path / "missing.jsonl")
        assert SCRIPT.main(["diff", missing, missing]) == 3

    def test_usage_error_exits_2(self) -> None:
        """No subcommand is a usage error."""
        assert SCRIPT.main([]) == 2


def test_window_is_the_train_split_in_whole_days() -> None:
    """The window runs from the Train start to the Train end date, at midnight UTC."""
    start, end = SCRIPT.train_window(
        _ROOT / "docs" / "experiments" / "swing-mvp1" / "data-manifest.json"
    )
    assert (start, end) == (datetime(2020, 9, 1, tzinfo=UTC), datetime(2024, 6, 30, tzinfo=UTC))
