"""Unit tests for scripts/backtest/run_portfolio_backtest.py helpers (#822).

Invariants (822-portfolio-backtest change.md D-1, D-5, R-5):

- ``--pairs`` accepts only distinct symbols from the signed §3 universe.
- Bars of all pairs are delivered by close time, the daily bars first on ties.
- The trial records the pairs, the shared capital, the active risk rule and
  the execution model, so a portfolio run is a distinct trial from a per-pair one.
- The report lists every rule the run does not apply.
- ``--funnel-evidence-out`` writes evidence ``hermes funnel`` accepts and
  logs every §8.5 replay as a trial; without it the run logs one trial and
  writes no evidence (v2-funnel-evidence R-1 to R-3).
- A v2 trial names its close-based exit pricing; a v1 trial keeps its shape
  (v2-strategy-config D-4).
"""

import argparse
import hashlib
import importlib.util
import io
import json
import random
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from hermes.adapters.files import LocalFiles
from hermes.adapters.trial_log.jsonl_file import JsonlTrialLog
from hermes.application.backtest.replay import ExecutionModel
from hermes.application.calibration.backtest_funnel import (
    parse_funnel_front_matter,
    validate_evidence,
)
from hermes.application.execution.prereg_book import ExitPricing
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints.funnel import load_funnel_evidence, read_front_matter
from hermes.entrypoints.funnel import main as funnel_main
from hermes.infrastructure.config.models import StrategyConfig
from tests.support.paths import REPO_ROOT
from tests.support.research_batch import BATCH_PATH, batch_front_matter, batch_text
from tests.support.research_sweep import sweep_front_matter
from tests.support.unknown_signal import config_with_unknown_signal, refusal_lines

_SCRIPTS = REPO_ROOT / "scripts" / "backtest"
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _import_driver() -> ModuleType:
    """Import the driver; it imports its sibling modules by name, as when run directly."""
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_portfolio_backtest_driver", _SCRIPTS / "run_portfolio_backtest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_portfolio_backtest_driver"] = module
    spec.loader.exec_module(module)
    return module


DRIVER = _import_driver()


def _bar(pair: Pair, interval: KlineInterval, close_time: datetime) -> Bar:
    span = timedelta(days=1) if interval is KlineInterval.D1 else timedelta(hours=4)
    return Bar(
        pair=pair,
        interval=interval,
        open_time=close_time - span,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        close_time=close_time,
    )


def test_default_pairs_are_the_signed_universe() -> None:
    """With no --pairs the run trades BTC, ETH and SOL (§3)."""
    assert DRIVER._parse_pairs("BTCUSDT,ETHUSDT,SOLUSDT") == (_BTC, _ETH, _SOL)


@pytest.mark.parametrize("text", ["", "BTCUSDT,BTCUSDT", "BTCUSDT,ADAUSDT"])
def test_pairs_outside_the_universe_or_repeated_are_refused(text: str) -> None:
    """An empty list, a repeat or a symbol outside §3 is a usage error."""
    with pytest.raises(argparse.ArgumentTypeError):
        DRIVER._parse_pairs(text)


def test_bars_merge_by_close_time_daily_first() -> None:
    """At midnight every pair's 1D bar goes before any 4h bar closing then (#311)."""
    midnight = _T0 + timedelta(days=1)
    btc_4h = _bar(_BTC, KlineInterval.H4, midnight)
    eth_1d = _bar(_ETH, KlineInterval.D1, midnight)
    btc_1d = _bar(_BTC, KlineInterval.D1, midnight)
    earlier = _bar(_SOL, KlineInterval.H4, midnight - timedelta(hours=4))
    merged = DRIVER._merge([btc_4h, eth_1d, btc_1d, earlier])
    assert merged == [earlier, btc_1d, eth_1d, btc_4h]


def test_trial_parameters_name_the_portfolio() -> None:
    """The trial records the pairs, shared capital, RO-01 and the execution model."""
    params = DRIVER._trial_parameters(
        {"donchian_period": 20}, ExecutionModel(depth_frac=Decimal("0.1")), (_BTC, _ETH)
    )
    assert params == {
        "donchian_period": 20,
        "pairs": "BTC/USDT,ETH/USDT",
        "portfolio": "shared-capital",
        "risk.rules": "RO-01",
        "execution.fill_model": "orderbook_walk",
        "execution.entry_bps": Decimal("5"),
        "execution.stop_bps": Decimal("15"),
        "execution.depth_frac": Decimal("0.1"),
    }


def test_v2_trial_parameters_name_the_close_based_exit_pricing() -> None:
    """A v2 portfolio trial adds ``execution.exit_pricing = bar_close`` to v1's fields."""
    model = ExecutionModel(depth_frac=Decimal("0.1"), exit_pricing=ExitPricing.BAR_CLOSE)
    params = DRIVER._trial_parameters({"donchian_period": 20}, model, (_BTC,))
    assert params["execution.exit_pricing"] == "bar_close"


def test_a_named_signal_is_recorded_in_the_trial() -> None:
    """A candidate's ``signal:`` is part of what ran, so its trial line names it (#1074)."""
    params = DRIVER._trial_parameters(
        {"lookback": 100}, ExecutionModel(depth_frac=Decimal("0.1")), (_BTC,), "daily-ma"
    )
    assert (params["signal"], params["lookback"]) == ("daily-ma", 100)


def test_no_signal_leaves_the_trial_without_a_signal_key() -> None:
    """A v1 or v2 trial line stays byte-identical to the lines logged before #1074."""
    params = DRIVER._trial_parameters(
        {"donchian_period": 20}, ExecutionModel(depth_frac=Decimal("0.1")), (_BTC,), None
    )
    assert "signal" not in params


def test_report_lists_every_rule_left_off() -> None:
    """Every SA-9 rule other than RO-01, and the kill evaluator, is listed as stubbed."""
    assert sorted(DRIVER.STUBBED) == [
        "RO-02 spread spike",
        "RO-03 max slippage",
        "RO-04 stale book",
        "RO-05/RO-06 depeg",
        "RO-07 macro ATR halt",
        "RO-08 portfolio drawdown",
        "kill evaluator",
    ]


# ---------------------------------------------------------------------------
# --funnel-evidence-out on a synthetic run (#997, v2-funnel-evidence R-1 to R-3)
# ---------------------------------------------------------------------------

_PRE_REG_V2 = REPO_ROOT / "docs" / "experiments" / "swing-mvp1" / "pre-registration-v2.md"
_V2 = "swing-mvp1-v2"
_WARMUP_FROM = datetime(2020, 3, 1, tzinfo=UTC)
_WINDOW = ("2021-01-01", "2021-04-01")
_ONE_MS = timedelta(milliseconds=1)


def _walk(pair: Pair, seed: int) -> list[Bar]:
    """Return a seeded, upward-drifting 4h walk and its daily bars, closing at the last ms."""
    rng = random.Random(seed)  # noqa: S311 — synthetic fixture, not crypto
    bars_4h: list[Bar] = []
    price = Decimal("100")
    for i in range((datetime(2021, 4, 2, tzinfo=UTC) - _WARMUP_FROM).days * 6):
        open_time = _WARMUP_FROM + timedelta(hours=4 * i)
        close = (price * Decimal(str(1 + rng.gauss(0.0015, 0.01)))).quantize(Decimal("0.0001"))
        wick = Decimal(str(1 + abs(rng.gauss(0, 0.004))))
        bars_4h.append(
            Bar(
                pair=pair,
                interval=KlineInterval.H4,
                open_time=open_time,
                open=price,
                high=(max(price, close) * wick).quantize(Decimal("0.0001")),
                low=(min(price, close) / wick).quantize(Decimal("0.0001")),
                close=close,
                volume=Decimal("1000"),
                close_time=open_time + timedelta(hours=4) - _ONE_MS,
            )
        )
        price = close
    days = [bars_4h[i : i + 6] for i in range(0, len(bars_4h), 6)]
    bars_1d = [
        Bar(
            pair=pair,
            interval=KlineInterval.D1,
            open_time=day[0].open_time,
            open=day[0].open,
            high=max(b.high for b in day),
            low=min(b.low for b in day),
            close=day[-1].close,
            volume=sum((b.volume for b in day), Decimal("0")),
            close_time=day[0].open_time + timedelta(days=1) - _ONE_MS,
        )
        for day in days
    ]
    return bars_4h + bars_1d


def _v2_roster() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": _V2,
            "enabled": True,
            "weight": "1.0",
            "capital_budget": "5000",
            "quote": "USDT",
            "exit_pricing": "bar_close",
            "sizing": {
                "risk_pct": "0.01",
                "max_position_pct": "0.05",
                "max_total_exposure_pct": "0.15",
            },
        }
    )


@dataclass(frozen=True)
class _SyntheticRun:
    exit_code: int
    trial_log: Path
    evidence: Path
    performance: Path


def _run_driver(
    tmp: Path,
    *,
    funnel: bool,
    extra: list[str] | None = None,
    selected: list[str | None] | None = None,
) -> _SyntheticRun:
    """Run the driver's main on synthetic bars, with the manifest, roster and split stubbed.

    Every roster name the driver asks for is appended to ``selected``.
    """
    asked = [] if selected is None else selected

    def select(_dir: Path, name: str | None, depth: Decimal) -> tuple[StrategyConfig, object]:
        asked.append(name)
        return _v2_roster(), _v2_roster().execution_model(depth_frac=depth)

    bars = DRIVER._merge(_walk(_BTC, 1) + _walk(_ETH, 2))
    datasets = [{"dataset_id": "synthetic", "content_sha256": "0" * 64}]

    async def load(*_args: object) -> tuple[list[Bar], list[dict[str, str]]]:
        return bars, datasets

    out = _SyntheticRun(
        exit_code=-1,
        trial_log=tmp / "trial-log.jsonl",
        evidence=tmp / "funnel-evidence.json",
        performance=tmp / "performance.json",
    )
    argv = [
        "--pairs=BTCUSDT,ETHUSDT",
        f"--start={_WINDOW[0]}",
        f"--end={_WINDOW[1]}",
        f"--trial-log={out.trial_log}",
        f"--output={tmp / 'report.json'}",
        f"--performance-out={out.performance}",
    ]
    if funnel:
        argv.append(f"--funnel-evidence-out={out.evidence}")
    argv += extra or []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(DRIVER, "_load_manifest", lambda _path: object())
        mp.setattr(DRIVER, "_load", load)
        mp.setattr(DRIVER, "trial_split", lambda *_args: DataSplit.TRAIN)
        mp.setattr(DRIVER, "_select", select)
        code = DRIVER.main(argv)
    return replace(out, exit_code=code)


@pytest.fixture(scope="module")
def funnel_run(tmp_path_factory: pytest.TempPathFactory) -> _SyntheticRun:
    """One synthetic run with --funnel-evidence-out, shared by the tests that read it."""
    return _run_driver(tmp_path_factory.mktemp("funnel"), funnel=True)


def test_funnel_run_exits_zero(funnel_run: _SyntheticRun) -> None:
    """A synthetic run with the flag completes."""
    assert funnel_run.exit_code == 0


def test_funnel_evidence_passes_the_hermes_funnel_parser(funnel_run: _SyntheticRun) -> None:
    """R-1: the file parses and validates against the signed v2 front matter's thresholds."""
    front_matter = read_front_matter(_PRE_REG_V2.read_text(encoding="utf-8"))
    thresholds = parse_funnel_front_matter(front_matter, strategy_id=_V2)
    evidence = load_funnel_evidence(funnel_run.evidence, strategy_id=_V2, files=LocalFiles())
    assert validate_evidence(evidence, thresholds) is evidence
    assert set(evidence.trials or {}) == {"base", *(evidence.neighbour_trial_ids or ())}


def test_hermes_funnel_judges_the_evidence(funnel_run: _SyntheticRun) -> None:
    """R-1: ``hermes funnel`` reads the file and prints verdicts: exit 0 or 1, never 3."""
    out, err = io.StringIO(), io.StringIO()
    code = funnel_main(
        [
            _V2,
            "--pre-registration",
            str(_PRE_REG_V2),
            "--evidence",
            str(funnel_run.evidence),
            "--expect-sha256",
            hashlib.sha256(_PRE_REG_V2.read_bytes()).hexdigest(),
        ],
        stream=out,
        err=err,
    )
    assert (code, err.getvalue()) in {(0, ""), (1, "")}
    assert out.getvalue().splitlines()[-1] in {"FUNNEL PASSED", "FUNNEL FAILED"}


def test_every_funnel_replay_is_a_trial_line(funnel_run: _SyntheticRun) -> None:
    """R-2: the base, six neighbours and the stress replay are each appended, in order."""
    records = JsonlTrialLog(funnel_run.trial_log).records()
    assert [r.parameters.get("funnel.replay", "base") for r in records] == [
        "base",
        "donchian_period=15",
        "donchian_period=25",
        "adx_threshold=20",
        "adx_threshold=30",
        "chandelier_atr_mult=2.5",
        "chandelier_atr_mult=3.5",
        "cost_stress=2",
    ]


def test_neighbour_trial_line_records_its_moved_parameter(funnel_run: _SyntheticRun) -> None:
    """A neighbour's trial line carries the parameter value it ran with."""
    records = JsonlTrialLog(funnel_run.trial_log).records()
    assert records[1].parameters["donchian_period"] == "15"
    assert records[0].parameters["donchian_period"] == "20"


def test_stress_trial_line_records_doubled_costs(funnel_run: _SyntheticRun) -> None:
    """The stress line shows twice the execution bps and the fee multiplier."""
    stress = JsonlTrialLog(funnel_run.trial_log).records()[-1]
    assert (
        stress.parameters["execution.entry_bps"],
        stress.parameters["execution.stop_bps"],
        stress.parameters["execution.cost_multiplier"],
    ) == ("10", "30", "2")


def test_performance_file_counts_every_logged_trial(funnel_run: _SyntheticRun) -> None:
    """The DSR deflates by every replay the run logged (v2-backtest-funnel Design §5.3)."""
    performance = json.loads(funnel_run.performance.read_text())
    assert performance["trials"]["count"] == 8


def test_evidence_daily_equity_is_the_base_run(funnel_run: _SyntheticRun) -> None:
    """The evidence's daily equity is the curve the performance file certifies."""
    evidence = json.loads(funnel_run.evidence.read_text())
    performance = json.loads(funnel_run.performance.read_text())
    assert evidence["daily_equity"] == performance["daily_equity"]
    assert evidence["start_date"] == _WINDOW[0]


def test_without_the_flag_one_trial_and_no_evidence(tmp_path: Path) -> None:
    """R-3: a run without the flag logs only the base trial and writes no evidence file."""
    run = _run_driver(tmp_path, funnel=False)
    records = JsonlTrialLog(run.trial_log).records()
    assert run.exit_code == 0
    assert [("funnel.replay" in r.parameters) for r in records] == [False]
    assert not run.evidence.exists()


def test_neighbour_and_stress_replays_run_their_own_configuration(
    funnel_run: _SyntheticRun,
) -> None:
    """A replay that ignored its override or its costs would copy the base run's curve."""
    evidence = json.loads(funnel_run.evidence.read_text())
    copies = [
        tid
        for tid in evidence["neighbour_trial_ids"]
        if evidence["trials"][tid] == evidence["trials"]["base"]
    ]
    assert copies == []
    assert evidence["cost_stress"]["daily_equity"] != evidence["daily_equity"]


# ---------------------------------------------------------------------------
# --batch --candidate (ADR-026 §2, research-funnel contract §2, issue #1057)
# ---------------------------------------------------------------------------


def _batch_file(tmp: Path, parameters: dict[str, object], status: str = "SIGNED") -> Path:
    """Write a two-candidate batch whose first candidate is v2 with ``parameters``."""
    fm = batch_front_matter()
    fm["status"] = status
    fm["candidates"][0].update(strategy_id=_V2, parameters=parameters)
    path = tmp / BATCH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(batch_text(fm), encoding="utf-8")
    return path


def _run_candidate(tmp: Path, parameters: dict[str, object]) -> _SyntheticRun:
    batch = _batch_file(tmp, parameters)
    return _run_driver(tmp, funnel=True, extra=[f"--batch={batch}", f"--candidate={_V2}"])


@pytest.fixture(scope="module")
def candidate_run(tmp_path_factory: pytest.TempPathFactory) -> _SyntheticRun:
    """One synthetic run of a batch candidate whose grid moves only the Donchian period."""
    return _run_candidate(
        tmp_path_factory.mktemp("candidate"),
        {"donchian_period": {"base": 20, "neighbours": [25]}},
    )


def test_a_candidate_run_uses_its_own_grid(candidate_run: _SyntheticRun) -> None:
    """Contract §2: the candidate's replays replace FUNNEL_REPLAYS: base, one neighbour, stress."""
    replays = [
        r.parameters.get("funnel.replay", "base")
        for r in JsonlTrialLog(candidate_run.trial_log).records()
    ]
    evidence = json.loads(candidate_run.evidence.read_text())

    assert (candidate_run.exit_code, replays, evidence["neighbour_trial_ids"]) == (
        0,
        ["base", "donchian_period=25", "cost_stress=2"],
        ["donchian_period=25"],
    )


def test_every_candidate_trial_line_names_its_batch(candidate_run: _SyntheticRun) -> None:
    """Contract §2: every trial line's parameters carry batch.id."""
    records = JsonlTrialLog(candidate_run.trial_log).records()

    assert {r.parameters.get("batch.id") for r in records} == {"b1"}


def test_a_v2_run_without_a_batch_names_no_batch(funnel_run: _SyntheticRun) -> None:
    """Without --batch nothing changes: no trial line carries batch.id."""
    records = JsonlTrialLog(funnel_run.trial_log).records()

    assert [("batch.id" in r.parameters) for r in records] == [False] * len(records)


def test_a_candidate_whose_signed_base_differs_from_the_strategy_is_refused(
    tmp_path: Path,
) -> None:
    """The run must start from the base the batch signed, or its trials test something else."""
    run = _run_candidate(tmp_path, {"donchian_period": {"base": 30, "neighbours": [25]}})

    assert (run.exit_code, run.trial_log.exists()) == (1, False)


def test_a_candidate_parameter_the_strategy_lacks_is_refused(tmp_path: Path) -> None:
    """Contract §1: parameter names are keys of the strategy's parameter mapping."""
    run = _run_candidate(tmp_path, {"no_such_knob": {"base": 1, "neighbours": [2]}})

    assert (run.exit_code, run.trial_log.exists()) == (1, False)


def test_a_candidate_the_batch_does_not_name_is_refused(tmp_path: Path) -> None:
    """--candidate must be one of the batch's strategy ids."""
    batch = _batch_file(tmp_path, {"donchian_period": {"base": 20, "neighbours": [25]}})

    run = _run_driver(tmp_path, funnel=True, extra=[f"--batch={batch}", "--candidate=swing-x"])

    assert (run.exit_code, run.trial_log.exists()) == (1, False)


def test_a_draft_batch_is_refused(tmp_path: Path) -> None:
    """ADR-026 §1: only a SIGNED batch runs; a DRAFT has no binding to count trials against."""
    batch = _batch_file(
        tmp_path, {"donchian_period": {"base": 20, "neighbours": [25]}}, status="DRAFT"
    )

    run = _run_driver(tmp_path, funnel=True, extra=[f"--batch={batch}", f"--candidate={_V2}"])

    assert (run.exit_code, run.trial_log.exists()) == (1, False)


def test_batch_without_candidate_is_a_usage_error(tmp_path: Path) -> None:
    """The two flags go together; argparse exits 2."""
    with pytest.raises(SystemExit) as caught:
        DRIVER.main([f"--batch={tmp_path / 'b1.md'}"])

    assert caught.value.code == 2


def test_an_unknown_signal_exits_1_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Contract §2a (#1119): an unknown ``signal:`` stops the driver at config load, exit 1."""
    config_dir = config_with_unknown_signal(tmp_path)

    code = DRIVER.main(
        [
            f"--config-dir={config_dir}",
            f"--trial-log={tmp_path / 'trial-log.jsonl'}",
            f"--output={tmp_path / 'report.json'}",
        ]
    )

    lines = capsys.readouterr().err.splitlines()
    assert (code, lines[0], lines[-1]) == (1, *refusal_lines(config_dir))


# ---------------------------------------------------------------------------
# --batch SWEEP --candidate POINT (ADR-031 §2, issue #1310)
# ---------------------------------------------------------------------------

_POINT = "breakout@donchian_period=25"


def _sweep_file(tmp: Path, grid: dict[str, list[object]]) -> Path:
    """Write a signed sweep whose breakout family is v2 with ``grid``."""
    fm = sweep_front_matter()
    fm["families"][0].update(template=_V2, grid=grid)
    path = tmp / BATCH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(batch_text(fm), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def point_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[_SyntheticRun, list[str | None]]:
    """One synthetic run of a sweep's grid point, and the roster names the driver asked for."""
    tmp = tmp_path_factory.mktemp("point")
    sweep = _sweep_file(tmp, {"donchian_period": [20, 25]})
    selected: list[str | None] = []
    run = _run_driver(
        tmp, funnel=True, extra=[f"--batch={sweep}", f"--candidate={_POINT}"], selected=selected
    )
    return run, selected


def test_a_grid_point_runs_its_familys_template(
    point_run: tuple[_SyntheticRun, list[str | None]],
) -> None:
    """ADR-031 §1: the roster entry is the family's template, not the point's id."""
    run, selected = point_run

    assert (run.exit_code, selected) == (0, [_V2])


def test_a_grid_points_trial_lines_carry_its_own_id_and_batch(
    point_run: tuple[_SyntheticRun, list[str | None]],
) -> None:
    """Every Train touch counts against the point itself (ADR-031 §2)."""
    records = JsonlTrialLog(point_run[0].trial_log).records()

    assert {(r.strategy_id, r.parameters["batch.id"]) for r in records} == {(_POINT, "b1")}


def test_a_grid_point_starts_from_its_own_value_and_moves_to_its_neighbour(
    point_run: tuple[_SyntheticRun, list[str | None]],
) -> None:
    """The base runs the point's value, 25, and the one neighbour runs the adjacent grid value."""
    records = JsonlTrialLog(point_run[0].trial_log).records()

    assert [
        (r.parameters.get("funnel.replay", "base"), r.parameters["donchian_period"])
        for r in records
    ] == [("base", "25"), ("donchian_period=20", "20"), ("cost_stress=2", "25")]


def test_a_grid_points_evidence_names_the_point(
    point_run: tuple[_SyntheticRun, list[str | None]],
) -> None:
    """The judge reads the evidence folder by candidate id, so the file must name it too."""
    evidence = json.loads(point_run[0].evidence.read_text())

    assert evidence["strategy_id"] == _POINT


def test_a_grid_point_on_a_parameter_the_template_lacks_is_refused(tmp_path: Path) -> None:
    """A point the template cannot run would log trials of something else, so nothing is logged."""
    sweep = _sweep_file(tmp_path, {"no_such_knob": [1, 2]})

    run = _run_driver(
        tmp_path, funnel=True, extra=[f"--batch={sweep}", "--candidate=breakout@no_such_knob=2"]
    )

    assert (run.exit_code, run.trial_log.exists()) == (1, False)


def test_a_point_the_sweep_does_not_expand_to_is_refused(tmp_path: Path) -> None:
    """--candidate must be one of the sweep's grid points."""
    sweep = _sweep_file(tmp_path, {"donchian_period": [20, 25]})

    run = _run_driver(
        tmp_path, funnel=True, extra=[f"--batch={sweep}", "--candidate=breakout@donchian_period=30"]
    )

    assert (run.exit_code, run.trial_log.exists()) == (1, False)
