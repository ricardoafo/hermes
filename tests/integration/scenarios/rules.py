"""Every pre-registration v1 rule, and every behaviour rule stated in code, with its proof.

``test_rule_list.py`` reads the bullets of §5.1 to §6.3 and §10 straight from
the signed document and fails when one has no entry here, when an entry names
a test that does not exist, or when a rule has neither a test nor a reason.

Each rule is in one of three states:

- ``proved_by``: tests that fail if the rule breaks. Scenario tests run the
  real serve; a unit test is listed only where no scenario can reach the rule.
- ``gap``: not wired into serve yet; names the open issue that tracks it.
- ``outside``: not an engine behaviour (a certification step, a procedure,
  or the live phase, which MVP1 does not run); says which.
"""

from __future__ import annotations

from dataclasses import dataclass

PRE_REGISTRATION = "docs/experiments/swing-mvp1/pre-registration-v1.md"
PRE_REGISTRATION_SHA256 = "4e3f832351318dfefec5ab952fbffcdfd319bab4aef2b97d7c0b800d627366ca"
"""Hash of the signed document this list was last reviewed against."""

_S = "tests/integration/scenarios/"
_OUTAGES = _S + "test_outages.py::"
_ENTRY = _S + "test_entries_and_exits.py::"
_RISK = _S + "test_risk_limits.py::"
_FAULTS = _S + "test_market_data_faults.py::"
_RESTART = _S + "test_restart.py::test_a_restart_with_a_position_open_keeps_it_until_its_exit"
_UNIT = "tests/unit/"
_EXEC_QUALITY = "tests/integration/entrypoints/test_serve_execution_quality.py::"
_SHADOW = _UNIT + "application/execution/test_shadow.py::"
_SHADOW_CHECKS = _UNIT + "application/strategy_protocol/test_shadow_checks.py::"
_SERVE_FILLS = "tests/integration/entrypoints/test_serve_fills.py::"


@dataclass(frozen=True)
class Rule:
    """One rule and how it is covered.

    Attributes:
        starts: The start of the rule's bullet in the document, or the rule's
            name for a rule stated in code.
        proved_by: Test node ids that fail when the rule breaks.
        gap: ``issue #N``, and what is missing, when the rule is not wired yet.
        outside: Why the rule is not an engine behaviour.

    """

    starts: str
    proved_by: tuple[str, ...] = ()
    gap: str = ""
    outside: str = ""


PRE_REGISTERED: dict[str, tuple[Rule, ...]] = {
    "5.1": (
        Rule(
            "**Donchian breakout**",
            proved_by=(
                _ENTRY + "test_a_breakout_opens_a_position_and_the_chandelier_stop_closes_it",
            ),
        ),
        Rule(
            "**Regime filter**", proved_by=(_ENTRY + "test_a_breakout_under_ema200_opens_nothing",)
        ),
        Rule(
            "**Momentum gate**",
            proved_by=(
                _ENTRY + "test_a_breakout_opens_a_position_and_the_chandelier_stop_closes_it",
                _ENTRY + "test_a_breakout_without_momentum_opens_nothing",
            ),
        ),
        Rule(
            "All three conditions TRUE",
            proved_by=(
                _ENTRY + "test_a_breakout_under_ema200_opens_nothing",
                _ENTRY + "test_a_breakout_without_momentum_opens_nothing",
            ),
        ),
    ),
    "5.2": (
        Rule(
            "**Chandelier Exit**",
            proved_by=(
                _ENTRY + "test_a_breakout_opens_a_position_and_the_chandelier_stop_closes_it",
                _RESTART,
            ),
        ),
        Rule(
            "**Time stop**",
            proved_by=(
                _ENTRY + "test_a_position_still_open_after_90_days_is_closed_by_the_time_stop",
                _RESTART,
            ),
        ),
        Rule(
            "**Regime break**",
            proved_by=(_ENTRY + "test_a_daily_close_under_ema200_closes_the_position", _RESTART),
        ),
    ),
    "5.3": (
        Rule(
            "Entry: **market order**",
            proved_by=(_ENTRY + "test_the_entry_size_price_and_fee_follow_the_pre_registration",),
        ),
        Rule(
            "Exit: **on-exchange stop-market**",
            gap=(
                "issue #1157: serve sells at market at the close of the bar that touches "
                "the stop; no resting stop"
            ),
        ),
    ),
    "5.4": (
        Rule(
            "**Risk per trade**",
            proved_by=(_ENTRY + "test_the_risk_budget_sets_the_size_when_the_stop_is_far",),
        ),
        Rule(
            "**Position notional**",
            proved_by=(
                _ENTRY + "test_the_entry_size_price_and_fee_follow_the_pre_registration",
                _ENTRY + "test_the_risk_budget_sets_the_size_when_the_stop_is_far",
            ),
        ),
        Rule(
            "**Hard cap**",
            proved_by=(_ENTRY + "test_the_cap_sets_the_size_when_the_stop_is_close",),
        ),
        Rule(
            "**Kelly deferred**",
            proved_by=(_ENTRY + "test_the_risk_budget_sets_the_size_when_the_stop_is_far",),
        ),
    ),
    "5.5": (
        Rule(
            "**Max concurrent positions**",
            proved_by=(_RISK + "test_three_positions_one_per_pair_can_be_open_together",),
        ),
        Rule(
            "**Correlation lock",
            proved_by=(
                _SHADOW + "test_serve_refuses_a_third_entry_correlated_with_two_held_pairs",
                _SHADOW
                + "test_serve_does_not_lock_the_third_entry_when_the_closes_cover_under_30_returns",
                _SHADOW + "test_serve_and_the_backtest_replay_read_the_same_correlations",
            ),
        ),
        Rule(
            "**Total exposure cap**",
            proved_by=(
                _RISK + "test_three_breakouts_fill_until_the_exposure_cap",
                _RISK + "test_the_exposure_cap_is_checked_at_marked_value_not_cost",
            ),
        ),
    ),
    "6.1": (
        Rule(
            "Binance retail tier (VIP0)",
            proved_by=(_ENTRY + "test_the_entry_size_price_and_fee_follow_the_pre_registration",),
        ),
        Rule(
            "**BNB discount OFF**",
            proved_by=(_ENTRY + "test_the_exit_sells_the_whole_position_into_the_live_book",),
        ),
        Rule("Fee history reconstructed", outside="data provenance for the backtest"),
    ),
    "6.2": (
        Rule("OHLCV-only backtest", outside="backtest data choice"),
        Rule(
            "Entry slippage: 5 bps",
            proved_by=(
                _ENTRY + "test_each_live_fill_records_the_pre_registered_model_price_beside_it",
            ),
        ),
        Rule(
            "Exit slippage on stop: 15 bps",
            proved_by=(
                _ENTRY + "test_each_live_fill_records_the_pre_registered_model_price_beside_it",
                _FAULTS + "test_an_exit_while_the_depth_endpoint_is_down_fills_on_the_model_book",
            ),
        ),
        Rule(
            "Shadow phase replaces this",
            proved_by=(_ENTRY + "test_the_exit_sells_the_whole_position_into_the_live_book",),
        ),
    ),
    "6.3": (
        Rule(
            "No partial fills",
            proved_by=(
                _UNIT + "adapters/fills/test_orderbook_walk.py::TestRejectionMatrix"
                "::test_insufficient_depth_sell_thin_bids",
            ),
        ),
        Rule(
            "No re-quote logic",
            proved_by=(
                _UNIT + "adapters/fills/test_orderbook_walk.py::TestRejectionMatrix"
                "::test_price_filter_limit_buy_above_best_ask",
            ),
        ),
    ),
    "10 backtest": (
        Rule(
            "DSR on Train+Val",
            outside="certification of the backtest (hermes certify), not a serve path",
        ),
        Rule(
            "Any secondary metric fails",
            outside="certification of the backtest (hermes certify), not a serve path",
        ),
        Rule(
            "Max drawdown ≥ 15% during train+val",
            proved_by=(
                _UNIT + "application/calibration/test_certification_gates.py"
                "::TestSection9Checks::test_each_row_fails_at_its_boundary",
            ),
        ),
    ),
    "10 shadow": (
        Rule(
            "Sim-vs-live PnL divergence",
            proved_by=(
                _SHADOW_CHECKS + "test_a_drift_median_above_30_bps_breaches_without_killing",
                _SHADOW_CHECKS + "test_the_drift_rule_ignores_direction",
                _SHADOW_CHECKS + "test_v1_exits_stay_out_of_the_drift_median_and_are_counted",
            ),
        ),
        Rule(
            "Any SA-9 shock overlay rule fires 3+",
            proved_by=(
                _SHADOW_CHECKS + "test_one_overlay_rule_halting_three_times_in_30_days_kills",
                _SERVE_FILLS
                + "test_a_daily_sa9_repeat_kill_is_recorded_alerted_and_stops_every_entry",
            ),
        ),
        Rule(
            "Shadow Sharpe < 0.3",
            proved_by=(
                _UNIT + "application/strategy_protocol/test_shadow_sharpe_kill.py"
                "::test_sixtieth_day_below_fires",
            ),
        ),
    ),
    "10 live": (
        Rule(
            "**Max drawdown 15% peak-to-trough**",
            proved_by=(
                _UNIT + "application/strategy_protocol/test_kill_trigger_evaluator.py"
                "::TestKillTriggerEvaluatorIndividualTriggers::test_max_drawdown_at_threshold_triggers",
            ),
        ),
        Rule(
            "Time-to-recovery > 90 days",
            gap="issue #1158: serve feeds days since the equity high as 0",
        ),
        Rule("30 rolling trades OOS Sharpe", outside="live phase; MVP1 runs shadow only"),
        Rule("Any SA-9 P0 rule violation", outside="live phase; MVP1 runs shadow only"),
        Rule("Two consecutive weeks of live fills", outside="live phase; MVP1 runs shadow only"),
    ),
    "10 post-kill": (
        Rule("Kill = strategy DEAD", outside="procedure after a kill"),
        Rule("Post-mortem within 7 days", outside="procedure after a kill"),
        Rule("New pre-registration required", outside="procedure after a kill"),
    ),
}


IN_CODE: tuple[Rule, ...] = (
    Rule(
        "Chandelier stop never moves down while a position is open",
        proved_by=(
            _UNIT + "domain/strategy/swing/test_stop_ratchet.py"
            "::TestStopNeverFallsInAPosition::test_the_stop_in_force_never_decreases_bar_to_bar",
            _UNIT + "domain/strategy/swing/test_stop_ratchet.py"
            "::TestStopHoldsWhenTheLevelFalls"
            "::test_the_exit_fires_on_the_held_stop_the_raw_level_would_miss",
        ),
    ),
    Rule(
        "Closed-bar rule: a bar is evaluated only after its close plus the settle delay",
        proved_by=(
            "tests/integration/fake_market/test_fake_market.py"
            "::test_the_real_adapter_delivers_each_bar_after_its_close_and_settle_delay",
        ),
    ),
    Rule(
        "Stale or missing book: a new entry is skipped",
        proved_by=(_FAULTS + "test_an_entry_while_the_depth_endpoint_is_down_is_skipped",),
    ),
    Rule(
        "Stale or missing book: an exit still fills",
        proved_by=(
            _FAULTS + "test_an_exit_while_the_depth_endpoint_is_down_fills_on_the_model_book",
        ),
    ),
    Rule(
        "Execution-quality breaches are alerted only: no decision, size or fill changes",
        proved_by=(
            _EXEC_QUALITY
            + "test_signals_fills_and_halts_are_the_same_whether_the_charts_breach_or_not",
            _EXEC_QUALITY + "test_the_slippage_breach_is_alerted_once_on_every_channel",
        ),
    ),
    Rule(
        "Chandelier stop clamped under the close",
        gap="issue #1157: the clamp the engine docstring describes is not implemented",
    ),
    Rule(
        "RO-03 slippage halt",
        proved_by=(_RISK + "test_a_fill_slipping_over_half_a_percent_halts_later_entries",),
    ),
    Rule(
        "RO-04 stale book halt",
        proved_by=(_FAULTS + "test_an_entry_while_the_depth_endpoint_is_down_is_skipped",),
    ),
    Rule(
        "RO-07 macro halt",
        proved_by=(_RISK + "test_a_btc_daily_drop_over_ten_percent_halts_entries_on_any_pair",),
    ),
    Rule(
        "RO-08 drawdown halt",
        proved_by=(
            _UNIT + "domain/risk/test_portfolio_drawdown.py"
            "::TestPortfolioDrawdownHalt::test_at_or_above_threshold_returns_halt",
        ),
    ),
    Rule("RO-02 spread spike", gap="issue #1158: serve has no one-hour median spread"),
    Rule("RO-05 and RO-06 depeg", gap="issue #1158: serve passes no stablecoin price"),
    Rule(
        "Late or missing bar: serve never handles a bar out of order",
        proved_by=(
            _FAULTS + "test_a_bar_published_a_little_late_is_handled_in_order",
            _FAULTS
            + "test_a_bar_published_after_the_next_one_stops_serve_and_a_restart_handles_it",
        ),
    ),
    Rule(
        "EUR values never block a fill and are priced once FX is back",
        proved_by=(
            _OUTAGES + "test_trades_made_while_fx_is_down_are_priced_in_euros_at_the_next_boot",
        ),
    ),
    Rule(
        "A notification outage never blocks a fill",
        proved_by=(_OUTAGES + "test_a_telegram_outage_does_not_stop_trading",),
    ),
)
