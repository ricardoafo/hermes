# Paper-Trading Engine — Research Vault

Persistent, in-repo documentation for the paper-trading engine research effort (2026-08-24).

Paper-trading is the FOUNDATIONAL subsystem of hermes. Every substantive design decision must survive the evidence collected here. Reports are dual-persisted: engram (searchable memory) + these markdown files (committable, reviewable, permanent).

## Sub-agent reports

| #     | Focus                                                                                                                                                    | File                                                       | Engram topic key                                  | Status  |
|-------|----------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------|---------------------------------------------------|---------|
| SA-0  | Landscape scouting — every paper-trading / backtest system that matters, ranked by success + failures                                                    | [sa0-landscape.md](sa0-landscape.md)                       | `research/paper-trading-sa0-landscape`            | ✅ Done |
| SA-1  | Academic execution simulation — Almgren-Chriss, Kyle, Obizhaeva-Wang, backtest fidelity theory                                                           | [sa1-academic.md](sa1-academic.md)                         | `research/paper-trading-sa1-academic`             | ✅ Done |
| SA-2  | Deep code reading — NautilusTrader, LEAN, Freqtrade, hftbacktest, Zipline, VectorBT                                                                      | [sa2-deep-code.md](sa2-deep-code.md)                       | `research/paper-trading-sa2-deep-code`            | ✅ Done |
| SA-3  | Microstructure — Binance + Kraken spot facts, orderbook streams, latency, fee tiers                                                                      | [sa3-microstructure.md](sa3-microstructure.md)             | `research/paper-trading-sa3-microstructure`       | ✅ Done |
| SA-4  | Failure post-mortems — Knight Capital + retail-scale + regulatory + Freqtrade / Zipline / LEAN specific bugs                                             | [sa4-post-mortems.md](sa4-post-mortems.md)                 | `research/paper-trading-sa4-post-mortems`         | ✅ Done |
| SA-5  | Calibration harness protocol — metrics, thresholds, statistical rigor, certification lifecycle                                                           | [sa5-calibration.md](sa5-calibration.md)                   | `research/paper-trading-sa5-calibration`          | ✅ Done |
| SA-6  | Multi-strategy portfolio management (MVP2-preparatory) — Allocator vs. Sub-portfolio vs. Capital-per-strategy                                            | [sa6-multi-strategy.md](sa6-multi-strategy.md)             | `research/paper-trading-sa6-multi-strategy`       | ✅ Done |
| SA-7  | Swing strategy design + risk management patterns — Donchian, ATR stops, position sizing, kill switches                                                   | [sa7-strategy-design.md](sa7-strategy-design.md)           | `research/paper-trading-sa7-strategy-design`      | ✅ Done |
| SA-8  | Reproducibility + audit trail — event sourcing, deterministic replay, immutable event logs                                                               | [sa8-reproducibility.md](sa8-reproducibility.md)           | `research/paper-trading-sa8-reproducibility`      | ✅ Done |
| SA-9  | Portfolio shocks + risk overlay — correlation regime, flash crash / liquidity vacuum, stablecoin depeg, 8 concrete rules                                 | [sa9-portfolio-shocks.md](sa9-portfolio-shocks.md)         | `research/paper-trading-sa9-portfolio-shocks`     | ✅ Done |
| SA-10 | Python performance patterns + testing philosophy — asyncio, GIL, serialization, SQLite tuning, Parquet, property invariants, chaos, mutation, CI pyramid | [sa10-python-perf-testing.md](sa10-python-perf-testing.md) | `research/paper-trading-sa10-python-perf-testing` | ✅ Done |

## Synthesis

| # | Focus                                                                                          | File                                                         | Engram topic key                   | Status  |
|---|------------------------------------------------------------------------------------------------|--------------------------------------------------------------|------------------------------------|---------|
| — | Synthesis: reconciled architecture, 20-decision ADR outline, 7 focused specs, 8 open decisions | [SYNTHESIS.md](SYNTHESIS.md)                                 | `research/paper-trading-synthesis` | ✅ Done |
| — | Synthesis amendment: additions to the synthesis after review, additive only                    | [SYNTHESIS-AMENDMENT.md](SYNTHESIS-AMENDMENT.md)             | —                                  | Draft   |
| — | Spec-readiness audit of the 9 specs the synthesis proposed                                     | [spec-readiness.md](spec-readiness.md)                       | —                                  | ✅ Done |
| — | Prior art: pre-registration discipline in quantitative finance before pre-registration v1      | [prior-art-preregistration.md](prior-art-preregistration.md) | —                                  | ✅ Done |

Drives the paper-trading ADR + focused specs.

## Locked decisions (context for all research)

- **MVP1 scope**: Swing strategy only. Portfolio Balancer deferred to MVP2.
- **Time model**: Hybrid — event-driven at fill layer (orderbook diff stream) + bar-close consolidator at strategy layer.
- **Data storage**: Orderbook diff storage (issue #203) supersedes kline-only storage (#139).
- **Same-code-path invariant**: Backtest + shadow-mode consume the SAME FillModelPort, differing only in MarketDataPort + ClockPort injections.
- **Two-balance architecture**: Domain aggregates carry `strategy_id` from day 1 (bucket-aware), even though MVP1 exercises one bucket.
- **#196 shadow-mode ADR** on hold — will be revised or reopened after paper-trading research lands.

## Rules for this folder

- Every SA report must be committable — cite sources with URLs, mark confidence H/M/L per claim category, include date + author metadata.
- No claim without a source. No source without a link (or a specific book/paper citation).
- If a finding contradicts an earlier locked decision, it MUST be flagged in the report — decisions can be revised, but not silently.
