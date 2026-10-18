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
_KLINE_ROWS = _U + "adapters/market_data/test_binance_live_klines_rows.py::"
_REPLAY = _I + "adapters/market_data/test_binance_live_klines_replay.py::"
_DEPTH = _U + "adapters/market_data/test_binance_depth_snapshot.py::"
_FX = _U + "adapters/fx/test_frankfurter.py::"
_TG_429 = _U + "adapters/notifier/test_telegram_adapter.py::TestRateLimitBackOff::"
_LOOP = _I + "entrypoints/test_serve_bar_loop.py::"
_CHAOS = _S + "test_chaos_crash_mid_bar.py::"
_FILLS = _I + "entrypoints/test_serve_fills.py::"
_TAX = _I + "entrypoints/test_serve_tax_guard.py::"
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
_CRASH = _S + "test_chaos_crash_mid_bar.py::"
_STORAGE = _S + "test_chaos_storage.py::"
_CLOCK = _S + "test_chaos_clock.py::"
_SHADOW = _U + "application/execution/test_shadow.py::"
_PORTFOLIO_MACHINE = _U + "application/portfolio/test_portfolio_risk_state_machine.py::"
_LEDGER_MACHINE = _U + "adapters/ledger/test_sqlite_ledger_state_machine.py::"
_BOOK_MACHINE = _U + "adapters/market_data/test_orderbook_diff_state_machine.py::"
_LOTS_MACHINE = _U + "domain/lots/test_fifo_lots_state_machine.py::"
_ALERTS = _U + "deploy/test_alert_scripts.py::"
_WIRE = _U + "adapters/test_wire_numbers.py::"
_PATHS = _S + "test_generated_paths.py::"
_DOWN = _S + "test_downtime_recovery.py::"
_READS = _U + "entrypoints/test_tui_reads.py::"
_LOTS_AGREE = _S + "test_portfolio_matches_ledger_lots.py::"
_KLINES_DOWN = _S + "test_chaos_klines_down_at_boot.py::"
_SIGTERM = _S + "test_chaos_sigterm_mid_bar.py::"
_EVERY_BAR = _PATHS + "test_every_rule_holds_after_every_bar_and_a_restart_changes_nothing"
_FUZZ_BINANCE = _U + "adapters/market_data/test_wire_fuzz_binance.py::"
_FUZZ_KRAKEN = _U + "adapters/exchanges/test_wire_fuzz_kraken.py::"
_BINANCE_SPOT = _U + "adapters/exchanges/test_binance_spot.py::"
_KRAKEN_SPOT = _U + "adapters/exchanges/test_kraken_spot.py::"
_FUZZ_LEDGER = _U + "adapters/ledger/test_reader_fuzz.py::"
_FUZZ_CONFIG = _U + "infrastructure/config/test_loader_fuzz.py::"
_CONTENTION = _I + "adapters/ledger/test_ledger_contention.py::"
_FILE_LOCK = _U + "infrastructure/test_file_lock.py::"
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
            "A market-data payload has the wrong shape: wrong types, missing keys, not an object",
            proved_by=(
                _FUZZ_BINANCE + "test_a_depth_stream_message_parses_or_raises_exchange_error",
                _FUZZ_BINANCE
                + "test_a_websocket_adapter_depth_snapshot_parses_or_raises_exchange_error",
                _FUZZ_BINANCE + "test_a_live_kline_row_parses_or_raises_exchange_error",
                _FUZZ_KRAKEN + "test_a_ticker_reply_parses_or_raises_kraken_public_error",
            ),
        ),
        FailureMode(
            "A historical candle is off the interval grid or has a price at or below zero",
            proved_by=(
                _FUZZ_BINANCE + "test_an_accepted_historical_kline_row_is_a_valid_kline",
                _FUZZ_BINANCE + "test_an_accepted_vision_kline_csv_row_is_a_valid_bar",
                _FUZZ_KRAKEN + "test_accepted_ohlc_candles_are_valid_klines",
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
            proved_by=(
                _FAULTS + "test_a_bar_that_is_never_published_stops_serve_once"
                "_and_the_restart_passes_over_it",
            ),
        ),
        FailureMode(
            "Serve was down longer than the warm-up window",
            proved_by=(
                _LOOP + "test_cursor_older_than_history_reports_the_lost_bars_and_carries_on",
            ),
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
            proved_by=(
                _KLINE_ROWS + "test_rows_in_any_order_come_out_ascending",
                _KLINE_ROWS + "test_an_exact_repeat_of_a_row_is_dropped",
                _KLINE_ROWS + "test_two_different_rows_for_one_bar_are_refused",
            ),
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
            proved_by=(
                _KLINES_DOWN + "test_klines_down_at_boot_exits_one_after_the_adapter_retries_only",
                _KLINES_DOWN + "test_the_boot_systemd_makes_after_a_klines_outage"
                "_trades_as_an_uninterrupted_run",
            ),
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
            proved_by=(
                _DEPTH + "test_a_book_whose_update_id_has_not_moved_keeps_its_first_stamp",
                _DEPTH + "test_a_book_whose_update_id_went_back_keeps_the_newest_stamp",
                _DEPTH + "test_a_book_that_moved_after_freezing_is_fresh_again",
            ),
        ),
        FailureMode(
            "A gapped or replayed depth diff reaches the book",
            proved_by=(_BOOK_MACHINE + "TestOrderbookDiffMachine::runTest",),
        ),
        FailureMode(
            "A depth diff older than the snapshot reaches the book",
            proved_by=(
                _BOOK_MACHINE + "test_a_diff_older_than_the_snapshot_never_reaches_the_book",
                _BOOK_MACHINE + "TestOrderbookDiffMachine::runTest",
            ),
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
            "A Kraken fee reply holds a percentage too large for decimal maths",
            proved_by=(
                _FUZZ_KRAKEN + "test_an_accepted_trade_volume_schedule_has_non_negative_fees",
                _FUZZ_KRAKEN + "test_an_accepted_asset_pairs_schedule_has_finite_non_negative_fees",
            ),
        ),
        FailureMode(
            "An order reply is lost, so the order state is unknown",
            proved_by=(
                _BINANCE_SPOT + "test_every_order_carries_a_fresh_client_order_id",
                _BINANCE_SPOT
                + "test_an_order_whose_reply_was_lost_returns_the_fill_found_by_its_client_id",
                _BINANCE_SPOT + "test_a_failed_lookup_names_the_client_id_to_reconcile",
                _BINANCE_SPOT + "test_a_stop_loss_whose_reply_was_lost_returns_the_found_order_id",
                _KRAKEN_SPOT + "test_every_order_carries_a_userref",
                _KRAKEN_SPOT + "test_a_failed_read_back_after_addorder_carries_the_txid",
                _KRAKEN_SPOT + "test_a_lost_addorder_reply_names_the_userref",
            ),
        ),
        FailureMode(
            "An order still in flight inside Binance reads as never placed on the first lookup",
            proved_by=(
                _BINANCE_SPOT + "test_an_order_binance_never_saw_is_looked_up_twice_a_wait_apart",
                _BINANCE_SPOT
                + "test_an_order_in_flight_at_the_first_lookup_returns_the_fill_found_on"
                "_the_recheck",
                _BINANCE_SPOT
                + "test_a_stop_loss_in_flight_at_the_first_lookup_returns_the_order_found"
                "_on_the_recheck",
            ),
        ),
        FailureMode(
            "A filled order is reported as an error",
            proved_by=(
                _BINANCE_SPOT + "test_third_asset_commission_is_priced_at_its_ticker",
                _BINANCE_SPOT + "test_third_asset_commission_falls_back_to_the_taker_rate",
            ),
        ),
        FailureMode(
            "The Kraken nonce goes backwards after a restart",
            proved_by=(
                _KRAKEN_SPOT
                + "test_a_restart_with_the_clock_stepped_back_still_sends_a_higher_nonce",
                _KRAKEN_SPOT + "test_a_bad_nonce_file_fails_an_order_before_sending_it",
            ),
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
            "The FX answer is for another base currency or a later day",
            proved_by=(
                _FX + "TestAnswerMatchesTheQuestion::test_a_response_for_another_base_raises",
                _FX
                + "TestAnswerMatchesTheQuestion::test_a_rate_dated_after_the_requested_day_raises",
            ),
        ),
        FailureMode(
            "The FX answer is dated well before the fill day",
            gap="issue #996: any earlier date is accepted; the allowed gap is a tax-rule call",
        ),
        FailureMode(
            "A slow FX service stalls the bar loop",
            gap="issue #996: a sync client runs on the event loop; inferred, untested",
        ),
    ),
    "ledger and disk": (
        FailureMode(
            "A ledger cell holds what Hermes never writes: NULL, a blob, NaN or a naive time",
            proved_by=(
                _FUZZ_LEDGER + "test_a_ledger_reader_returns_or_raises_ledger_error",
                _FUZZ_LEDGER + "test_a_ledger_reader_accepts_only_finite_numbers_and_aware_times",
            ),
        ),
        FailureMode(
            "A trial log or decision journal file is not UTF-8 or a line has the wrong shape",
            proved_by=(
                _U + "adapters/trial_log/test_wire_fuzz_jsonl_file.py"
                "::test_a_rechained_trial_line_reads_or_raises_trial_log_error",
                _U + "adapters/trial_log/test_wire_fuzz_jsonl_file.py"
                "::test_a_trial_log_that_is_not_json_lines_raises_trial_log_error",
                _U + "adapters/journal/test_wire_fuzz_jsonl_store.py"
                "::test_a_rechained_journal_entry_reads_or_raises_integrity_error",
                _U + "adapters/journal/test_wire_fuzz_jsonl_store.py"
                "::test_a_journal_that_is_not_json_lines_raises_integrity_error",
            ),
        ),
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
            proved_by=(
                _CRASH + "test_a_crash_mid_bar_leaves_only_whole_transactions_on_disk",
                _CRASH + "test_a_restart_after_a_crash_mid_bar_records_each_trade_once",
            ),
        ),
        FailureMode(
            "The disk fills during a ledger write",
            proved_by=(
                _STORAGE + "test_a_full_disk_at_boot_stops_serve_with_exit_1_and_the_cause_logged",
                _STORAGE + "test_a_full_disk_at_boot_commits_no_row",
                _STORAGE + "test_a_storage_error_mid_bar_stops_serve"
                "_with_exit_1_and_the_cause_logged",
                _STORAGE + "test_a_storage_error_mid_bar_commits_no_part_of_the_trade",
                _STORAGE + "test_a_restart_after_a_storage_error_mid_bar_trades_once",
            ),
        ),
        FailureMode(
            "A torn last line in the trial log or decision journal",
            proved_by=(
                _S + "test_chaos_torn_append.py"
                "::test_a_trial_append_killed_halfway_is_repaired_and_the_next_run_records",
                _S + "test_chaos_torn_append.py"
                "::test_a_journal_append_killed_halfway_is_repaired_and_the_next_decision_records",
                _U + "entrypoints/test_trials.py"
                "::test_without_confirm_a_torn_log_is_refused_and_left_as_it_was",
                _U + "adapters/trial_log/test_jsonl_file.py"
                "::test_a_repair_refuses_a_log_edited_before_the_torn_line",
            ),
        ),
        FailureMode(
            "Another process holds the ledger while serve commits",
            proved_by=(
                _CONTENTION
                + "test_a_serve_write_lands_at_once_while_a_reader_holds_a_read_transaction",
                _STORAGE + "test_serve_waits_out_a_write_lock_held_by_another_process",
                _U + "adapters/ledger/test_connection.py::"
                "test_every_ledger_connection_waits_out_a_lock",
            ),
        ),
        FailureMode(
            "A reader holds the ledger past serve's 10 s busy wait",
            proved_by=(
                _CONTENTION
                + "test_a_serve_write_lands_at_once_while_a_reader_holds_a_read_transaction",
                _U + "adapters/ledger/test_connection.py::"
                "test_a_read_write_connection_puts_the_ledger_in_wal",
            ),
        ),
        FailureMode(
            "A read-only unit opens the ledger while serve is stopped (WAL files gone)",
            proved_by=(
                _U + "adapters/ledger/test_connection.py::"
                "test_closing_the_last_writer_keeps_the_wal_and_shm_files",
                _U + "adapters/ledger/test_connection.py::"
                "test_a_read_only_unit_opens_the_ledger_while_serve_is_stopped",
                _U + "entrypoints/test_tax.py::test_a_ledger_it_cannot_switch_to_wal_exits_1",
            ),
        ),
        FailureMode(
            "Two processes append to the trial log at once",
            proved_by=(
                _U + "adapters/trial_log/test_jsonl_file.py::"
                "test_an_append_waits_while_another_appender_holds_the_lock",
                _U + "adapters/trial_log/test_jsonl_file.py::"
                "test_concurrent_appenders_leave_one_unbroken_chain",
            ),
        ),
        FailureMode(
            "Two processes append to the decision journal at once",
            proved_by=(
                _U + "application/journal/test_service.py::"
                "test_an_append_waits_while_another_appender_holds_the_lock",
                _U + "application/journal/test_service.py::"
                "test_concurrent_journal_appenders_leave_one_unbroken_chain",
            ),
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
            "Restart, migration or snapshot restore loses or doubles a ledger row",
            proved_by=(_LEDGER_MACHINE + "TestSqliteLedgerMachine::runTest",),
        ),
        FailureMode(
            "Two migration snapshots taken in one second overwrite each other",
            proved_by=(
                _LEDGER_MACHINE + "test_a_second_snapshot_never_overwrites_the_first",
                _U
                + "adapters/ledger/test_migration_rollback.py::"
                + "test_a_second_snapshot_in_the_same_second_gets_its_own_name",
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
            proved_by=(
                _SERVE + "TestPortfolioRebuiltOnStartup"
                "::test_a_file_that_is_not_a_ledger_raises_startup_error_naming_it",
            ),
        ),
        FailureMode(
            "The ledger path is wrong or the disk is not mounted",
            proved_by=(
                _SERVE
                + "TestLedgerAndSeedGuards::test_a_missing_ledger_is_refused_and_not_created",
                _U + "entrypoints/test_cli.py::TestServeExitCodes"
                "::test_new_ledger_reaches_the_build_only_when_given",
            ),
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
            proved_by=(
                _CLOCK + "test_a_clock_jump_is_recorded_as_off_then_back",
                _CLOCK
                + "test_a_clock_jumped_ahead_across_the_breakout_close_leaves_the_entry_halted",
                _I
                + "entrypoints/test_serve_clock_skew.py::"
                + "test_a_clock_five_minutes_ahead_is_recorded_and_alerted_once",
                _U
                + "application/status/test_report.py::"
                + "test_a_clock_left_off_turns_the_status_red_with_the_fix",
            ),
        ),
        FailureMode(
            "The wall clock steps backwards",
            proved_by=(
                _CLOCK + "test_a_clock_jump_across_a_bar_close_handles_every_bar_exactly_once",
                _CLOCK + "test_a_clock_jump_across_a_bar_close_sends_the_entry_push_once",
            ),
        ),
    ),
    "process": (
        FailureMode(
            "Two serves run on the same ledger",
            proved_by=(
                _STORAGE + "test_a_second_serve_on_a_ledger_in_use_refuses_to_run",
                _STORAGE + "test_hermes_serve_on_a_ledger_in_use_exits_with_the_refusal_code",
                _STORAGE + "test_the_serve_lock_is_released_when_the_first_serve_exits",
                _FILE_LOCK + "test_a_holder_process_that_is_killed_frees_the_lock",
            ),
        ),
        FailureMode(
            "A restart with a position open",
            proved_by=(
                _S + "test_restart.py::test_a_restart_with_a_position_open_keeps_it_until_its_exit",
                _LOOP + "test_restart_while_held_writes_the_same_stop_marks_as_an_unbroken_run",
            ),
        ),
        FailureMode(
            "A lab shadow runs on another shadow's ledger and mixes their capital and trades",
            proved_by=(
                _S + "test_shadow_line.py::"
                "test_a_shadow_pointed_at_another_shadows_ledger_is_refused_before_writing",
                _S + "test_shadow_line.py::test_each_shadow_ledger_holds_only_its_own_strategy",
                _UNITS + "test_shadow_template_runs_the_instance_strategy_on_its_own_ledger",
            ),
        ),
        FailureMode(
            "A shadow instance names a strategy the roster lacks and runs the enabled one instead",
            proved_by=(
                _U + "entrypoints/test_cli.py::TestServeSubcommand::"
                "test_serve_with_a_strategy_missing_from_the_roster_exits_three",
            ),
        ),
        FailureMode(
            "An entry on a bar that closed while serve was down fills back-dated or halts",
            proved_by=(
                _DOWN + "test_an_entry_on_a_bar_that_closed_while_serve_was_down_is_not_filled",
                _FILLS + "test_bar_re_evaluated_after_a_crash_fills_nothing_twice",
            ),
        ),
        FailureMode(
            "An exit on a bar that closed while serve was down is dated at the old close",
            proved_by=(
                _DOWN
                + "test_an_exit_on_a_bar_that_closed_while_serve_was_down_fills_when_serve_is_back",
                _SHADOW + "test_a_missed_exit_fills_on_the_live_book_stamped_when_serve_is_back",
                _SHADOW + "test_a_missed_exit_without_a_live_book_is_stamped_now_on_the_newest_bar",
            ),
        ),
        FailureMode(
            "Bars missed during an outage go unrecorded, or the operator is not told",
            proved_by=(
                _DOWN + "test_bars_missed_over_a_day_are_recorded_per_stream_and_reported_once",
                _U + "application/monitoring/test_restart_report.py::"
                "test_the_report_counts_missed_bars_per_pair_and_stream",
            ),
        ),
        FailureMode(
            "A quick restart treats live bars as missed",
            proved_by=(_DOWN + "test_a_restart_inside_the_live_window_misses_nothing",),
        ),
        FailureMode(
            "An outage day raises parity mismatches for its unfilled missed entries",
            proved_by=(
                _U
                + "application/parity/test_check.py::test_a_day_with_missed_bars_is_not_compared",
                _U + "entrypoints/test_parity.py::"
                "test_an_outage_day_prints_not_compared_and_sends_no_alert",
            ),
        ),
        FailureMode(
            "Serve boots before the clock syncs and misreads which bars it missed",
            proved_by=(_UNITS + "test_serve_starts_after_the_clock_is_synced",),
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
            proved_by=(
                _SIGTERM + "test_sigterm_mid_bar_finishes_the_poll_in_flight_and_exits_zero",
                _SIGTERM + "test_sigterm_mid_bar_exits_before_systemd_s_stop_timeout"
                "_even_when_every_call_is_slow",
            ),
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
        FailureMode(
            "A slow ledger or journal read freezes the console's keys and screen switches",
            proved_by=(_READS + "test_a_screen_switch_paints_before_a_slow_read_returns",),
        ),
        FailureMode(
            "Redraws asked for during a slow read are lost, or pile up as one read each",
            proved_by=(
                _READS + "test_redraws_during_a_held_read_run_one_more_read_with_the_newest_state",
            ),
        ),
        FailureMode(
            "A ledger connection opened on the console's UI thread fails in its read worker",
            proved_by=(
                _U + "entrypoints/test_tui_tax.py::"
                "test_the_tax_source_reads_the_ledger_from_the_console_worker_thread",
                _U + "adapters/ledger/test_connection.py::"
                "test_an_any_thread_read_only_connection_answers_another_thread",
            ),
        ),
        FailureMode(
            "Quitting the console while a read is in flight raises an error",
            proved_by=(_READS + "test_quitting_while_a_read_is_held_ends_quietly",),
        ),
        FailureMode(
            "A new activity row arriving while another fades leaves a highlight that never ends",
            proved_by=(
                _U + "entrypoints/test_tui_activity_fade.py::"
                "test_a_second_new_row_in_mid_fade_restarts_the_fade_and_still_ends",
            ),
        ),
        FailureMode(
            "An activity read that fails while rows fade leaves a stale highlight or no error",
            proved_by=(
                _U + "entrypoints/test_tui_activity_fade.py::"
                "test_a_failed_activity_read_in_mid_fade_shows_the_error_and_no_highlight",
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
            "A config file is not UTF-8, nests too deep or refers to itself through an alias",
            proved_by=(
                _FUZZ_CONFIG + "test_a_config_file_that_is_not_yaml_raises_config_error",
                _FUZZ_CONFIG + "test_an_operator_file_that_is_not_yaml_raises_config_error",
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
            "A staging env file holds a trading key or a loopback endpoint",
            proved_by=(
                _RELEASE + "test_real_config_check_refuses_a_staging_env_file_that_breaks_the_tier",
                _RELEASE + "test_staging_deploy_stops_on_a_config_finding_before_any_restart",
                _U + "entrypoints/test_config_check.py::TestEnvFile"
                "::test_stdin_env_file_with_a_trading_key_names_it",
            ),
        ),
        FailureMode(
            "The env file a staging deploy checks is missing or not UTF-8",
            proved_by=(
                _U + "entrypoints/test_config_check.py::TestEnvFile"
                "::test_missing_env_file_exits_three",
                _U + "entrypoints/test_config_check.py::TestEnvFile"
                "::test_env_file_that_is_not_utf8_exits_three",
                _RELEASE + "test_staging_deploy_stops_when_the_config_check_cannot_run",
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
            proved_by=(
                _SERVE + "TestPortfolioRebuiltOnStartup"
                "::test_a_ledger_a_newer_release_migrated_raises_startup_error_naming_the_migration",
                _S + "test_chaos_storage.py"
                "::test_serve_refuses_a_ledger_a_newer_release_has_migrated",
            ),
        ),
        FailureMode(
            "HERMES_ENV is missing on the production box",
            proved_by=(
                _UNITS
                + "test_hermes_service_runs_as_prod_when_the_environment_file_omits_hermes_env",
            ),
        ),
        FailureMode(
            "The health ping reports green while hermes status is broken",
            proved_by=(
                _ALERTS + "test_health_ping_reports_a_status_argument_error_as_fail_with_the_error",
            ),
        ),
        FailureMode(
            "capital_budget is changed after the first boot",
            proved_by=(
                _SERVE + "TestLedgerAndSeedGuards"
                "::test_a_capital_that_differs_from_the_recorded_seed_is_refused_naming_both",
            ),
        ),
    ),
    "notifications": (
        FailureMode(
            "A tax-guard breach (ADR-002 Decision 6) is never recorded or alerted",
            proved_by=(
                _TAX
                + "test_one_trade_past_the_ceiling_records_one_critical_row_at_the_daily_close",
                _TAX + "test_one_trade_past_the_ceiling_is_alerted_once_on_the_routed_channel",
                _FILLS + "test_a_tax_guard_breach_is_recorded_and_changes_no_decision_or_fill",
            ),
        ),
        FailureMode(
            "A tax-guard breach is recorded twice for one daily close, across pairs or a restart",
            proved_by=(
                _TAX + "test_two_pairs_closing_the_same_day_record_the_breach_once",
                _TAX + "test_a_restart_that_re_handles_the_second_pairs_bar_records_nothing_new",
            ),
        ),
        FailureMode(
            "The tax guard cannot read the ledger and stops serve",
            proved_by=(
                _TAX
                + "test_a_guard_that_cannot_read_the_ledger_is_logged_and_the_bar_still_processed",
            ),
        ),
        FailureMode(
            "Telegram is down",
            proved_by=(
                _OUTAGES + "test_a_telegram_outage_does_not_stop_trading",
                _LOOP + "test_notifier_outage_does_not_stop_the_loop",
            ),
        ),
        FailureMode(
            "A Telegram reply has the wrong shape, so a poll crashes hermes bot",
            proved_by=(
                _U + "adapters/telegram_bot/test_wire_fuzz_telegram.py"
                "::test_a_get_updates_reply_parses_or_raises_telegram_bot_error",
                _U + "adapters/telegram_bot/test_wire_fuzz_telegram.py"
                "::test_a_send_message_reply_returns_or_raises_telegram_bot_error",
            ),
        ),
        FailureMode(
            "A crash after the signal row means the trade is never pushed",
            proved_by=(
                _CHAOS + "test_a_crash_mid_bar_does_not_lose_the_entry_push",
                _CHAOS + "test_a_crash_between_the_push_and_its_record_pushes_the_entry_once_more",
            ),
        ),
        FailureMode(
            "A non-infrastructure error in the notifier stops serve after the fill",
            proved_by=(
                _LOOP
                + "test_a_notifier_bug_does_not_stop_serve_and_the_push_is_retried_on_the_next_bar",
                _LOOP + "test_a_settled_push_is_not_retried_after_a_restart",
                _U
                + "application/test_signal_notifier.py::"
                + "test_a_non_infra_exception_is_isolated_and_the_fan_out_goes_on",
            ),
        ),
        FailureMode(
            "A signal is pushed twice after a crash",
            proved_by=(
                _LOOP + "test_crash_between_signal_row_and_cursor_row_does_not_notify_twice",
                _CHAOS + "test_a_crash_mid_bar_does_not_lose_the_entry_push",
            ),
        ),
        FailureMode(
            "Telegram answers 429",
            proved_by=(
                _TG_429 + "test_one_429_then_ok_delivers_the_message_once",
                _TG_429 + "test_retry_after_in_the_body_is_the_wait",
                _TG_429 + "test_repeated_429s_give_up_after_three_retries",
                _TG_429 + "test_waits_never_add_up_past_the_cap",
            ),
        ),
        FailureMode(
            "A slow Telegram holds up the bar that sent the push",
            gap=(
                "issue #996: the push runs in a thread, but serve awaits it before it "
                "finishes the bar (up to 30 s per channel on 429); inferred, untested"
            ),
        ),
    ),
    "risk halts": (
        FailureMode(
            "Cash, equity, stops or refusals drift under halt, kill and restart",
            proved_by=(_PORTFOLIO_MACHINE + "TestPortfolioRiskMachine::runTest",),
        ),
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
            proved_by=(
                _FILLS + "test_a_halted_entry_is_recorded_once_and_alerted_once",
                _FILLS + "test_a_halt_changes_no_decision_and_its_entry_is_not_pushed",
                _S
                + "test_suspected_defects.py::"
                + "test_an_entry_refused_by_a_risk_halt_is_not_announced",
            ),
        ),
        FailureMode(
            "RO-03 loses its input or stays latched for good",
            proved_by=(
                _CHAOS + "test_a_restart_after_a_crash_mid_bar_leaves_the_ledger"
                "_an_uninterrupted_run_leaves",
                _TRADES + "TestRecordTrade"
                "::test_record_trade_stores_its_further_events_in_the_same_commit",
                _SHADOW + "test_a_slipping_fill_older_than_the_window_no_longer_halts_entries",
                _RISK + "test_a_fill_slipping_over_half_a_percent"
                "_no_longer_halts_entries_four_days_later",
            ),
        ),
        FailureMode(
            "The RO-03 cumulative slippage limit never fires",
            proved_by=(
                _SHADOW
                + "test_fills_inside_the_window_that_slip_over_2_percent_together_halt_entries",
                _SHADOW
                + "test_slips_older_than_the_window_do_not_count_toward_the_cumulative_limit",
            ),
        ),
        FailureMode(
            "RO-08 drawdown and time-to-recovery are never driven through serve",
            gap="issue #1158: serve feeds days since the equity high as 0",
        ),
        FailureMode(
            "A halt survives, or is lost across, a restart",
            proved_by=(
                _S + "test_restart.py"
                "::test_a_restart_during_an_ro07_halt_keeps_it_until_btc_closes_a_steady_day",
                _S + "test_restart.py"
                "::test_a_restart_during_an_ro03_halt_keeps_it_until_a_fill_slips_less",
            ),
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
            proved_by=(
                _PATHS + "test_after_a_restart_open_positions_keep_their_last_marks",
                _EVERY_BAR,
            ),
        ),
        FailureMode(
            "A coin is held with no entry signal or entry price",
            proved_by=(
                _S + "test_restart.py"
                "::test_a_coin_held_without_its_entry_signal_is_watched_after_a_restart",
                _S + "test_restart.py"
                "::test_a_coin_held_without_its_entry_signal_is_logged_with_its_first_buy",
                _U + "entrypoints/test_serve_held_position.py"
                "::test_the_entry_is_the_close_of_the_bar_the_buy_filled_after",
            ),
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
            "The entry spread pushes total exposure past the cap",
            proved_by=(
                _U + "application/execution/test_kernel.py"
                "::test_an_entry_that_fits_the_cap_only_at_the_signal_price_is_declined",
                _RISK
                + "test_three_entries_that_make_the_cap_at_the_signal_price_overrun_it_at_the_ask",
                _PORTFOLIO_MACHINE + "TestPortfolioRiskMachine::runTest",
            ),
        ),
        FailureMode(
            "A position built by two entries on one pair cannot be sold",
            proved_by=(_PORTFOLIO_MACHINE + "test_an_exit_sells_a_position_built_by_two_entries",),
        ),
        FailureMode(
            "Tax lots across several assets lose or invent quantity",
            proved_by=(_LOTS_MACHINE + "TestFifoLotsMachine::runTest",),
        ),
        FailureMode(
            "Lot selection draws a lot dated after the sale",
            proved_by=(_LOTS_MACHINE + "test_fifo_never_draws_a_lot_opened_after_the_sale",),
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
            proved_by=(
                _LOTS_AGREE + "test_after_five_round_trips_the_portfolio_equals_the_ledger_lots",
                _LOTS_AGREE + "test_on_every_generated_path_the_portfolio_equals_the_ledger_lots",
            ),
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
            "Warm-up with fewer than 300 daily bars decides otherwise than 300",
            proved_by=(
                _META + "test_the_configured_floor_of_300_daily_bars_decides_what_400_decide",
                _U
                + "infrastructure/config/test_market_data_config.py::"
                + "TestMarketDataConfigWarmupFloors::"
                + "test_market_data_rejects_warmup_below_the_floor",
                _U
                + "entrypoints/test_parity.py::"
                + "test_offline_with_299_daily_bars_exits_three_naming_the_300_bar_floor",
            ),
        ),
    ),
}
