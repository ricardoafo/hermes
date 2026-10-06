"""Every way we know Hermes can fail, with its proof, its open issue or Rick's acceptance.

``test_failure_mode_list.py`` fails when an entry has no answer, when a gap
names no issue, when an accepted risk has no dated approval from Rick, or when
an entry names a test that does not exist.

The list starts from a hater pass over serve, the adapters and the ledger
(issue #839): for each, how could it fail, before asking whether a test shows
it does not. Every PR that changes runtime code adds the failure modes it
finds here, and every bug fixed adds its entry and the test that would have
caught it.

Each failure mode is in one of three states:

- ``proved_by``: tests that fail if Hermes stops handling it.
- ``gap``: ``issue #N: what happens today``. Says "inferred" when nobody has
  run it yet.
- ``accepted``: ``approved by Rick YYYY-MM-DD: why we live with it``.
"""

from __future__ import annotations

from dataclasses import dataclass

_U = "tests/unit/"
_I = "tests/integration/"
_S = _I + "scenarios/"
_FAULTS = _S + "test_market_data_faults.py::"
_OUTAGES = _S + "test_outages.py::"
_RISK = _S + "test_risk_limits.py::"
_LIVE = _U + "adapters/market_data/test_binance_live_klines.py::"
_REPLAY = _I + "adapters/market_data/test_binance_live_klines_replay.py::"
_DEPTH = _U + "adapters/market_data/test_binance_depth_snapshot.py::"
_FX = _U + "adapters/fx/test_frankfurter.py::"
_LOOP = _I + "entrypoints/test_serve_bar_loop.py::"
_FILLS = _I + "entrypoints/test_serve_fills.py::"
_SERVE = _U + "entrypoints/test_serve.py::"
_UNITS = _U + "deploy/test_systemd_units.py::"
_RELEASE = _I + "deploy/test_release_scripts.py::"
_LEDGER = _U + "adapters/ledger/test_sqlite_ledger.py::"
_TRADES = _U + "adapters/ledger/test_sqlite_trade_ledger.py::"
_ROLLBACK = _U + "adapters/ledger/test_sqlite_ledger_rollback_any_error.py::"
_MIGRATE = _U + "adapters/ledger/test_migration_rollback.py::"
_INTEGRITY = _U + "adapters/ledger/test_integrity.py::"
_SNAPSHOT = _U + "domain/portfolio/"
_FIFO = _U + "domain/lots/test_eur_fifo.py::"
_WIRE = _U + "adapters/test_wire_numbers.py::"
_PATHS = _S + "test_generated_paths.py::"
_EVERY_BAR = _PATHS + "test_every_rule_holds_after_every_bar_and_a_restart_changes_nothing"
_META = _I + "metamorphic/test_backtest_relations.py::"


@dataclass(frozen=True)
class FailureMode:
    """One way Hermes can fail and how it is answered.

    Attributes:
        name: What goes wrong, in plain words.
        proved_by: Test node ids that fail when Hermes stops handling it.
        gap: ``issue #N: ...`` when it is not handled or not proved yet.
        accepted: ``approved by Rick YYYY-MM-DD: ...`` when we live with it.

    """

    name: str
    proved_by: tuple[str, ...] = ()
    gap: str = ""
    accepted: str = ""


AREAS = (
    "market data",
    "exchange and FX",
    "ledger and disk",
    "clock",
    "process",
    "config and deploy",
    "notifications",
    "risk halts",
    "money",
    "backtest and replay",
)

CATALOGUE: dict[str, tuple[FailureMode, ...]] = {
    "market data": (
        FailureMode(
            "A wire value is NaN, infinite or an impossible timestamp",
            proved_by=(
                _WIRE + "test_a_non_finite_number_is_refused_with_the_adapters_own_error",
                _WIRE + "test_a_live_kline_time_past_the_calendar_is_an_exchange_error",
                _WIRE + "test_a_kraken_kline_time_past_the_calendar_is_a_kraken_error",
                _FAULTS
                + "test_a_closed_bar_with_a_nan_close_stops_serve_loudly_and_a_restart_handles_it",
            ),
        ),
        FailureMode(
            "A NaN in the forming candle blocks the closed bar before it",
            proved_by=(
                _FAULTS
                + "test_a_nan_in_the_forming_candle_does_not_block_the_closed_bar_before_it",
            ),
        ),
        FailureMode(
            "A candle is never published",
            gap="issue #852: serve exits 1 on every boot and systemd gives up (reproduced)",
        ),
        FailureMode(
            "Serve was down longer than the warm-up window",
            gap="issue #852: the cursor is older than the history, so every boot exits 1",
        ),
        FailureMode(
            "A bar is published late but in time",
            proved_by=(
                _FAULTS + "test_a_bar_published_a_little_late_is_handled_in_order",
                _LIVE + "TestOverdueBars"
                "::test_bar_published_20_minutes_late_is_delivered_when_it_appears",
            ),
        ),
        FailureMode(
            "A bar is published after the next one",
            proved_by=(
                _FAULTS
                + "test_a_bar_published_after_the_next_one_stops_serve_and_a_restart_handles_it",
            ),
        ),
        FailureMode(
            "The same bar is delivered twice",
            proved_by=(
                _LIVE + "TestDelivery::test_each_bar_is_emitted_once_across_overlapping_polls",
                _SERVE + "TestBarLoop::test_a_repeated_bar_is_skipped",
            ),
        ),
        FailureMode(
            "One klines response holds duplicate or unsorted rows",
            gap="issue #842: the last duplicate wins silently; inferred, no test feeds such rows",
        ),
        FailureMode(
            "The forming candle is delivered as closed",
            proved_by=(
                _LIVE + "TestDelivery::test_feed_never_emits_the_open_candle",
                _REPLAY + "TestRecordedFixtures"
                "::test_recorded_4h_sample_drops_the_forming_candle_and_keeps_wire_values",
            ),
        ),
        FailureMode(
            "A candle has impossible values (low over high, zero price, wrong close time)",
            proved_by=(
                _LIVE + "TestWireEdgeCases::test_invalid_candle_fields_are_permanent_errors",
                _LIVE + "TestErrorPolicy"
                "::test_close_time_that_is_not_open_plus_interval_minus_1ms_is_permanent",
            ),
        ),
        FailureMode(
            "Klines answer with a transport error or 5xx mid-run",
            proved_by=(
                _LIVE + "TestErrorPolicy"
                "::test_transport_error_is_retried_three_times_with_100_200_400_ms_backoff",
                _LIVE + "TestErrorPolicy"
                "::test_retries_exhausted_degrades_and_repolls_in_5_seconds_without_raising",
            ),
        ),
        FailureMode(
            "Klines answer 429 or 418",
            proved_by=(
                _LIVE + "TestErrorPolicy"
                "::test_rate_limit_responses_are_not_retried_and_wait_retry_after",
                _LIVE + "TestWireEdgeCases"
                "::test_retry_after_is_parsed_clamped_or_replaced_by_the_retry_interval",
            ),
        ),
        FailureMode(
            "Klines are down or answer an error page while serve boots",
            gap="issue #846: serve exits 1 with no in-process retry; inferred, no serve-level test",
        ),
        FailureMode(
            "An outage spans a bar boundary",
            proved_by=(
                _LIVE + "TestOutageBackfill::test_backfill_after_outage_leaves_no_gap",
                _REPLAY + "TestOutageScenario"
                "::test_90_minute_outage_across_a_boundary_yields_the_same_sequence",
            ),
        ),
        FailureMode(
            "The feed stalls and answers nothing",
            proved_by=(
                _LIVE + "TestOverdueBars"
                "::test_source_goes_stale_while_a_bar_is_overdue_and_recovers_after",
                _U + "entrypoints/test_status.py::test_overdue_ledger_exits_one_and_says_late",
            ),
        ),
        FailureMode(
            "The depth endpoint is down, slow or malformed",
            proved_by=(
                _DEPTH + "test_a_malformed_body_raises_exchange_error",
                _DEPTH + "test_a_transport_failure_raises_network_error",
                _FAULTS + "test_an_entry_while_the_depth_endpoint_is_down_is_skipped",
                _FAULTS + "test_an_exit_while_the_depth_endpoint_is_down_fills_on_the_model_book",
            ),
        ),
        FailureMode(
            "A cached or frozen depth book counts as fresh",
            gap="issue #859: the book is stamped on arrival; inferred, untested",
        ),
    ),
    "exchange and FX": (
        FailureMode(
            "An exchange answers 429, 5xx or malformed JSON",
            proved_by=(
                _U + "adapters/exchanges/test_binance_spot.py"
                "::test_rate_limit_reply_is_not_retried",
                _U + "adapters/exchanges/test_kraken_spot.py::test_rate_limit_error_is_not_retried",
                _U + "adapters/exchanges/test_kraken_spot.py"
                "::test_malformed_replies_raise_exchange_error",
                _U + "adapters/exchanges/test_binance_klines.py::TestFetchErrorPaths"
                "::test_http_500_raises_exchange_error",
            ),
        ),
        FailureMode(
            "An order reply is lost, so the order state is unknown",
            gap="issue #858: a Binance 5xx reads as a refusal and no client order id exists",
        ),
        FailureMode(
            "A filled order is reported as an error",
            gap="issue #858: a commission in a third asset raises after the fill",
        ),
        FailureMode(
            "The Kraken nonce goes backwards after a restart",
            gap="issue #858: the nonce lives only in memory; inferred, untested",
        ),
        FailureMode(
            "The FX service is down or answers garbage",
            proved_by=(
                _OUTAGES + "test_trades_made_while_fx_is_down_are_priced_in_euros_at_the_next_boot",
                _FX + "TestErrorPaths::test_non_json_response_raises_fx_rate_error",
                _FX + "TestErrorPaths::test_http_500_raises_fx_rate_error",
            ),
        ),
        FailureMode(
            "The FX answer lacks the currency",
            proved_by=(_FX + "TestErrorPaths::test_missing_target_currency_raises_fx_rate_error",),
        ),
        FailureMode(
            "A weekend or holiday rate is stored as final too early",
            proved_by=(
                _U + "application/ledger/test_eur_pricer.py"
                "::test_prior_day_rate_on_the_fill_day_is_not_final",
                _U + "application/ledger/test_eur_pricer.py"
                "::test_weekend_fill_takes_friday_once_the_weekend_is_past",
            ),
        ),
        FailureMode(
            "The FX answer has the wrong date or base currency",
            gap="issue #859: neither is checked; inferred, untested",
        ),
        FailureMode(
            "A slow FX service stalls the bar loop",
            gap="issue #859: a sync client runs on the event loop; inferred, untested",
        ),
    ),
    "ledger and disk": (
        FailureMode(
            "A crash lands a trade, its lot and its event only partly",
            proved_by=(
                _TRADES + "TestRecordTrade::test_record_trade_is_one_commit",
                _TRADES + "TestRecordTrade::test_sell_beyond_inventory_stores_nothing",
                _LEDGER + "TestI06SingleTransactionPerWrite"
                "::test_open_lot_produces_exactly_one_commit_boundary",
            ),
        ),
        FailureMode(
            "A SQL error mid-write leaves part of the write",
            proved_by=(
                _LEDGER + "TestSqliteRollbackOnError::test_open_lot_duplicate_id_rolls_back",
                _LEDGER + "TestConsumeLotRollbackOnSqlError"
                "::test_consume_lot_rollback_leaves_no_consumption_row",
            ),
        ),
        FailureMode(
            "A non-SQL error mid-write leaves rows the next write commits",
            proved_by=(
                _ROLLBACK + "test_a_non_sqlite_error_in_a_write_leaves_nothing_for_the_next_commit",
                _ROLLBACK + "TestErrorsFromTheWritesOwnData"
                "::test_record_trade_whose_base_fee_is_its_quantity_stores_nothing",
                _ROLLBACK + "test_a_migration_failing_on_a_non_sqlite_error"
                "_leaves_nothing_for_the_next_commit",
                _S + "test_chaos_storage.py"
                "::test_a_non_sqlite_error_mid_write_leaves_nothing_for_the_next_commit",
            ),
        ),
        FailureMode(
            "A crash after the trade, before the cursor, fills the bar twice",
            proved_by=(_FILLS + "test_bar_re_evaluated_after_a_crash_fills_nothing_twice",),
        ),
        FailureMode(
            "A real kill or power loss lands mid-transaction",
            gap="issue #846: crashes are only simulated by deleting cursor rows",
        ),
        FailureMode(
            "The disk fills during a ledger write",
            gap="issue #846: inferred rollback and exit 1; no test fills a real disk",
        ),
        FailureMode(
            "A torn last line in the trial log or decision journal",
            gap="issue #846: detected, but every later append is blocked; no recovery path",
        ),
        FailureMode(
            "Another process holds the ledger while serve commits",
            gap="issue #855: no WAL and no busy timeout, so serve exits 1; inferred",
        ),
        FailureMode(
            "Two processes append to the trial log at once",
            gap="issue #855: no file lock, so the hash chain forks; inferred",
        ),
        FailureMode(
            "A migration fails halfway",
            proved_by=(
                _MIGRATE + "test_half_failed_migration_leaves_no_trace",
                _U + "adapters/ledger/test_migration_runner.py::TestApplyMigrationsFailureRollback"
                "::test_broken_sql_rolls_back_and_leaves_no_row",
            ),
        ),
        FailureMode(
            "A new release boots an older ledger",
            proved_by=(
                _S + "test_older_ledgers.py"
                "::test_serve_upgrades_an_older_ledger_and_trades_as_on_a_fresh_one",
                _MIGRATE + "test_destructive_migration_never_runs_at_start",
            ),
        ),
        FailureMode(
            "A migration is rolled back by restoring the snapshot",
            proved_by=(
                _MIGRATE + "test_migrate_snapshots_before_applying_and_restore_inverts_it",
                _MIGRATE + "test_corrupt_snapshot_is_not_restored",
            ),
        ),
        FailureMode(
            "Ledger rows disagree (orphan lot, wrong remainder)",
            proved_by=(
                _INTEGRITY + "test_consumption_of_a_missing_lot_is_an_orphan",
                _INTEGRITY + "test_lot_naming_a_missing_trade_is_an_orphan",
                _INTEGRITY + "test_wrong_remainder_is_a_finding",
            ),
        ),
        FailureMode(
            "The ledger file is corrupt when serve starts",
            gap="issue #856: a raw sqlite error escapes instead of a clear startup error",
        ),
        FailureMode(
            "The ledger path is wrong or the disk is not mounted",
            gap="issue #856: serve creates a fresh ledger and drops the open position; inferred",
        ),
    ),
    "clock": (
        FailureMode(
            "The exchange or local clock is ahead and closes a bar early",
            proved_by=(
                _LIVE + "TestExchangeClock::test_exchange_clock_ahead_never_advances_closure",
                _LIVE + "TestExchangeClock"
                "::test_exchange_clock_behind_delays_delivery_until_both_clocks_agree",
            ),
        ),
        FailureMode(
            "A bar is checked exactly at its close plus the settle delay",
            proved_by=(
                _U + "domain/market_data/test_bar_schedule.py::TestIsClosed"
                "::test_four_hour_bar_closes_exactly_at_boundary_plus_settle",
                _I + "fake_market/test_fake_market.py"
                "::test_the_real_adapter_delivers_each_bar_after_its_close_and_settle_delay",
            ),
        ),
        FailureMode(
            "Daylight saving time shifts a boundary",
            proved_by=(
                _U + "domain/market_data/test_bar_schedule.py::TestBoundaries"
                "::test_daily_boundary_is_utc_midnight",
                _U + "infrastructure/test_time.py::TestDstBoundary"
                "::test_roundtrip_across_dst_boundary",
            ),
        ),
        FailureMode(
            "The local clock is more than five minutes fast",
            gap="issue #859: every entry is halted on a stale book and status stays green",
        ),
        FailureMode(
            "The wall clock steps backwards",
            gap="issue #846: sleeps, cursors and the push window misbehave; inferred, untested",
        ),
    ),
    "process": (
        FailureMode(
            "Two serves run on the same ledger",
            gap="issue #855: no single-instance lock; both can push and fill; inferred",
        ),
        FailureMode(
            "A restart with a position open",
            proved_by=(
                _S + "test_restart.py::test_a_restart_with_a_position_open_keeps_it_until_its_exit",
                _LOOP + "test_restart_while_held_writes_the_same_stop_marks_as_an_unbroken_run",
            ),
        ),
        FailureMode(
            "SIGTERM while idle",
            proved_by=(
                _LOOP + "test_sigterm_while_idle_closes_every_resource_and_exits_zero",
                _SERVE + "TestInstallSignalHandlers"
                "::test_install_signal_handlers_sets_shutdown_on_sigterm",
            ),
        ),
        FailureMode(
            "SIGTERM while a bar is being handled",
            gap="issue #846: slow handling can outlast systemd's stop timeout; untested",
        ),
        FailureMode(
            "A runtime failure exits with the code that makes systemd restart",
            proved_by=(
                _SERVE + "TestRun::test_every_runtime_failure_returns_one_never_two_or_three",
                _LOOP + "test_unrecoverable_feed_error_exits_one_after_closing_resources",
            ),
        ),
        FailureMode(
            "Serve crash-loops",
            proved_by=(
                _UNITS + "test_hermes_service_start_limit_trips_inside_its_interval",
                _UNITS + "test_hermes_service_starts_the_failure_notifier_when_it_fails",
            ),
        ),
        FailureMode(
            "Serve hangs: the process lives but handles no bars",
            proved_by=(
                _U + "entrypoints/test_status.py::test_overdue_ledger_exits_one_and_says_late",
                _U + "deploy/test_alert_scripts.py"
                "::test_health_ping_reports_a_late_check_as_fail_with_the_status_body",
            ),
        ),
    ),
    "config and deploy": (
        FailureMode(
            "The config directory or a file is missing",
            proved_by=(
                _U + "entrypoints/test_cli.py::TestServeSubcommand"
                "::test_serve_missing_config_dir_exits_three",
                _UNITS + "test_hermes_service_does_not_restart_on_argument_or_config_errors",
            ),
        ),
        FailureMode(
            "Mainnet is switched on by mistake",
            proved_by=(
                _RELEASE + "test_preflight_refuses_every_spelling_that_enables_mainnet",
                _U + "infrastructure/config/test_loader.py"
                "::test_load_config_rejects_mainnet_outside_prod",
                _SERVE + "TestMainnetNeedsLiveApproval::test_refused_without_a_record",
            ),
        ),
        FailureMode(
            "A release is half installed",
            proved_by=(
                _RELEASE + "test_hash_mismatch_removes_the_half_built_release",
                _RELEASE + "test_rollback_refuses_a_previous_that_was_never_fully_installed",
                _RELEASE + "test_failed_gate_restores_the_old_current",
            ),
        ),
        FailureMode(
            "A deploy or rollback puts older code on a newer ledger",
            proved_by=(
                _RELEASE + "test_deploy_of_a_release_older_than_the_ledger_is_refused",
                _RELEASE + "test_rollback_onto_a_release_older_than_the_ledger_is_refused",
            ),
        ),
        FailureMode(
            "Older code is started on a newer ledger outside the deploy script",
            gap="issue #856: unknown applied migrations are ignored at boot; inferred",
        ),
        FailureMode(
            "HERMES_ENV is missing on the production box",
            gap="issue #856: serve runs as dev, so a missing alert channel is not refused",
        ),
        FailureMode(
            "The health ping reports green while hermes status is broken",
            gap="issue #856: exit 2 from status is read as an old release",
        ),
        FailureMode(
            "capital_budget is changed after the first boot",
            gap="issue #856: the portfolio sizes from config while the ledger keeps the old seed",
        ),
    ),
    "notifications": (
        FailureMode(
            "Telegram is down",
            proved_by=(
                _OUTAGES + "test_a_telegram_outage_does_not_stop_trading",
                _LOOP + "test_notifier_outage_does_not_stop_the_loop",
            ),
        ),
        FailureMode(
            "A crash after the signal row means the trade is never pushed",
            gap="issue #854: the restart sees the signal as old; inferred",
        ),
        FailureMode(
            "A non-infrastructure error in the notifier stops serve after the fill",
            gap="issue #854: only InfraError is isolated; inferred",
        ),
        FailureMode(
            "A signal is pushed twice after a crash",
            proved_by=(
                _LOOP + "test_crash_between_signal_row_and_cursor_row_does_not_notify_twice",
            ),
        ),
        FailureMode(
            "Telegram answers 429 or is slow",
            gap="issue #859: no retry, the message is lost and the bar loop waits",
        ),
    ),
    "risk halts": (
        FailureMode(
            "RO-03, RO-04 and RO-07 halt entries in serve",
            proved_by=(
                _RISK + "test_a_fill_slipping_over_half_a_percent_halts_later_entries",
                _FAULTS + "test_an_entry_while_the_depth_endpoint_is_down_is_skipped",
                _RISK + "test_a_btc_daily_drop_over_ten_percent_halts_entries_on_any_pair",
            ),
        ),
        FailureMode(
            "A halt is silent and the refused entry is still pushed as ENTRY",
            gap="issue #690: nothing is recorded or alerted; the push goes out regardless",
        ),
        FailureMode(
            "RO-03 loses its input or stays latched for good",
            gap="issue #857: the quality row is a separate commit and nothing clears the latch",
        ),
        FailureMode(
            "The RO-03 cumulative slippage limit never fires",
            gap="issue #857: the cumulative value is always 0",
        ),
        FailureMode(
            "RO-08 drawdown and time-to-recovery are never driven through serve",
            gap="issue #514: serve feeds days since the equity high as 0",
        ),
        FailureMode(
            "A halt survives, or is lost across, a restart",
            gap="issue #844: no test restarts during an RO-03 or RO-07 halt",
        ),
    ),
    "money": (
        FailureMode(
            "Decimal rounding breaks the accounting identity",
            proved_by=(
                _SNAPSHOT + "test_snapshot_properties.py"
                "::test_equity_equals_start_plus_realized_plus_unrealized",
                _SNAPSHOT + "test_snapshot.py::TestPartialSell"
                "::test_partial_sell_keeps_the_identity_exact_after_rounding",
                _FIFO + "TestWorkedExamples::test_three_way_split_adds_up_to_the_lot_value_exactly",
            ),
        ),
        FailureMode(
            "Cash goes negative",
            proved_by=(
                _SNAPSHOT + "test_snapshot_properties.py::test_cash_is_never_negative",
                _EVERY_BAR,
                _SNAPSHOT + "test_snapshot.py::TestRefusedFills"
                "::test_buy_whose_fee_tips_it_over_cash_is_refused",
                _U + "adapters/ledger/test_sqlite_capital_seed.py::TestQuoteLeg"
                "::test_buy_beyond_the_seed_is_refused",
            ),
        ),
        FailureMode(
            "Exposure goes over the cap",
            proved_by=(
                _RISK + "test_three_breakouts_fill_until_the_exposure_cap",
                _RISK + "test_the_exposure_cap_is_checked_at_marked_value_not_cost",
                _EVERY_BAR,
            ),
        ),
        FailureMode(
            "A trade risks more than 1% or sizes over the 5% cap on any generated path",
            proved_by=(_EVERY_BAR,),
        ),
        FailureMode(
            "Generated paths with outages, late bars and restarts break a rule",
            proved_by=(_EVERY_BAR,),
        ),
        FailureMode(
            "The cap and drawdown read entry prices right after a restart",
            gap="issue #863: positions are marked at their fill price until their next bar",
        ),
        FailureMode(
            "A coin is held with no entry signal or entry price",
            gap="issue #844: the strategy is seeded flat, so no exit rule watches it; inferred",
        ),
        FailureMode(
            "Fees are charged wrongly",
            proved_by=(
                _S + "test_entries_and_exits.py"
                "::test_the_entry_size_price_and_fee_follow_the_pre_registration",
                _FIFO + "TestWorkedExamples::test_round_trip_gain_is_net_of_both_fees",
            ),
        ),
        FailureMode(
            "Tax lots are consumed out of order or beyond inventory",
            proved_by=(
                _FIFO + "TestWorkedExamples::test_sell_spanning_two_lots_consumes_the_oldest_first",
                _TRADES + "TestRecordTrade::test_sell_beyond_inventory_stores_nothing",
            ),
        ),
        FailureMode(
            "The portfolio and the ledger lots drift apart in a run",
            gap="issue #358: no scenario compares tracker cash with the USDT lots after serve",
        ),
        FailureMode(
            "Splitting a sale in two changes the tax lots or the total gain",
            proved_by=(_FIFO + "test_splitting_a_sale_in_two_changes_no_lot_and_no_total_gain",),
        ),
        FailureMode(
            "EUR values do not scale with the prices behind them",
            proved_by=(
                _FIFO + "test_scaling_every_price_and_fee_by_k_scales_every_eur_value_by_k",
            ),
        ),
        FailureMode(
            "A tax lot loses, makes up or double-sells units",
            proved_by=(_FIFO + "test_every_unit_bought_is_either_consumed_once_or_still_open",),
        ),
    ),
    "backtest and replay": (
        FailureMode(
            "Two runs of one path write different ledgers",
            proved_by=(_META + "test_the_same_seed_twice_gives_a_byte_identical_ledger",),
        ),
        FailureMode(
            "A restart between two halves of a run changes its trades",
            proved_by=(_META + "test_ninety_days_equal_forty_five_plus_forty_five_with_a_restart",),
        ),
        FailureMode(
            "A pair that never signals changes another pair's trades",
            proved_by=(
                _META + "test_adding_a_pair_that_never_signals_changes_nothing_for_the_others",
            ),
        ),
        FailureMode(
            "The order of the pairs in the config changes the trades",
            proved_by=(_META + "test_reordering_the_pairs_in_the_config_changes_nothing",),
        ),
        FailureMode(
            "The calendar date of a path, not its bars, changes the trades",
            proved_by=(
                _META + "test_shifting_a_path_by_whole_days_shifts_every_trade_by_the_same_time",
            ),
        ),
        FailureMode(
            "The price level, not the moves, changes quantities or P&L",
            proved_by=(
                _META
                + "test_scaling_prices_and_capital_by_k_scales_money_by_k_and_keeps_quantities",
            ),
        ),
        FailureMode(
            "A decision reads a bar that has not closed yet (lookahead)",
            proved_by=(_META + "test_cutting_the_data_after_a_bar_changes_no_decision_up_to_it",),
        ),
        FailureMode(
            "Warm-up past the strategy's buffers changes a decision",
            proved_by=(_META + "test_more_warm_up_than_the_indicators_need_changes_no_decision",),
        ),
        FailureMode(
            "Warm-up at the configured floor of 200 daily bars decides otherwise than 300",
            gap=(
                "issue #880: reproduced, the EMA(200) is seeded from the oldest buffered "
                "closes; strict xfail "
                "test_the_configured_floor_of_200_daily_bars_decides_what_300_decide"
            ),
        ),
    ),
}
