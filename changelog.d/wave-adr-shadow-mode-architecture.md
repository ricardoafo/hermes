### Docs

- **Shadow-mode foundation — design phase (docs-only)**. Ships two
  companion artifacts for the #175 implementation (per ADR-009 §6
  dual-source rule) plus the resolutions from two rounds of
  industry-practice research (Freqtrade / Hummingbot / QuantConnect /
  Alpaca / IBKR / Binance testnet):
  - `docs/decisions/2026-08-24-shadow-mode-architecture.md` (ADR): 11
    locked design decisions. D1 emphasizes venue-agnostic
    `ShadowGate(<any ExchangePort>)` wrapping (Binance today, Kraken
    if we swap venues). D2 fee model uses per-venue `FeeSchedulePort`
    (from Kraken PR #193), never hardcodes rates — plus MVP1-hard-fails
    on BNB burn (guard lives in `binance_startup_guard.py`, not in
    `shadow_gate.py`). D9 two-file SQLite (Freqtrade PR #2697 pattern —
    single-table `shadow` BOOL rejected as anti-pattern). D10 `ClockPort`
    abstraction (avoids silent backtest timestamp bugs). D11 24h global
    unfilled_timeout. MVP2 revisit anchors for BNB fee-tier drift,
    SIGHUP kill-switch upgrade, three-tier divergence detection.
    Go-live checklist lives in the GH Project "Go-Live Prep" column,
    not a repo file (operator preference — single source of truth).
  - `openspec/changes/shadow-mode-foundation/change.md` + `issue.md`
    (SDD change spec): 13 testable requirements (R-1 through R-13) with
    ~40 shell-verifiable acceptance criteria, file-tree diff, interface
    stubs (venue-agnostic `ShadowGate` with per-venue `FeeSchedulePort`
    injection), Alembic double-target design locked to Option 1 (single
    `env.py`, env-var-driven URL, deploy invokes migrate twice).
    Applies operator directive 2026-08-24 (engram
    `preferences/openspec-change-required-per-pr`) and dogfoods the
    #194 DoR/DoD enforcement gate merged same-session.
