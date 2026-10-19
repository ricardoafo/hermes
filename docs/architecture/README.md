# Architecture decision records

Decisions about what Hermes is built from and why. Per
[ADR-009](adr-009-documentation-strategy.md) §1, an ADR is immutable once
Accepted: a change is a new ADR that supersedes the old one, and the old one
gets a "Superseded by" line.

## Index

The table is generated from each ADR's title, `**Status**:` line and
"Superseded by" lines by `scripts/dev/regenerate-adr-index.py`. Run the
script in the PR that adds an ADR or changes one of those lines;
`tests/unit/test_docs_indexes.py` fails when an ADR is missing from the
table.

<!-- adr-index-start -->
| ADR | Title | Status | Superseded by |
| --- | ----- | ------ | ------------- |
| [001](adr-001-llm-as-supervisor.md) | The LLM supervises; it does not trade | Accepted | — |
| [002](adr-002-tax-posture.md) | Tax posture — automated own-capital trading declared under the savings base, with dynamic FIFO in EUR | Accepted | [ADR-016](adr-016-tax-estimate-rules.md) (Decision item 1, rate scale only) |
| [003](adr-003-solution-architecture/) | Solution Architecture | Accepted | — |
| [004](adr-004-exchange-decision.md) | Exchange decision — Binance primary via ARG-DNI, Kraken hot backup, agnostic port at the code boundary | Accepted | — |
| [005](adr-005-ai-ml-support-boundary.md) | AI/ML support boundary — ML supports the deterministic engine, monitors our behavior, and assists development; it never generates trade decisions | Accepted | — |
| [006](adr-006-versioning-strategy.md) | Versioning strategy — SemVer for the release surface, numbered SQL migrations for the ledger, versioned config schemas for parameters | Accepted | [ADR-014](adr-014-pre-registration-binds-real-capital.md) (pre-release calendar), [ADR-022](adr-022-release-versions-and-deploy-cadence.md) (§1 "Version bump rules", release-cut mechanics) and [ADR-027](adr-027-zero-major-versions-until-real-capital.md) (`0.y.z` and `1.0.0-shadow.N` pre-release lines) |
| [007](adr-007-testing-strategy.md) | Testing strategy — pytest + property tests + snapshot tests + targeted mutation testing, with SEV-1 protection at every layer | Accepted | [ADR-017](adr-017-test-tiers-as-enforced.md) (tier cadences, mutation tool and scope, coverage table, test data) |
| [008](adr-008-observability-strategy.md) | Observability strategy — structlog to journald, healthchecks.io dead-man's switch, Telegram alerts, no metrics stack | Accepted | — |
| [009](adr-009-documentation-strategy.md) | Documentation strategy — ADRs for decisions, dated decisions journal for parameters, runbook for incidents, guides for how-to, docstrings sparingly | Accepted | — |
| [010](adr-010-dependency-management-and-supply-chain.md) | Dependency management + supply chain — uv-managed lockfile, Renovate for updates, safety + pip-audit for CVE scanning, documented response process | Accepted | decision-journal entries [Dependabot over Renovate](../decisions/2026-08-21-dependabot-over-renovate.md) (Renovate), [Drop `safety`](../decisions/2026-08-22-drop-safety.md) (§3, `safety` + `pip-audit` becomes `pip-audit`) and [VPS installs from hashed requirements](../decisions/2026-10-02-vps-install-from-hashed-requirements.md) (§1, VPS install step) |
| [011](adr-011-paper-trading-engine.md) | Paper-trading engine — hexagonal fill kernel with same-code-path backtest / shadow / live parity, bucket-aware domain, calibration as CI gate, risk overlay, and pre-registered Strategy Development Protocol | Accepted | [ADR-013](adr-013-shadow-composition-root.md) (D-02, the location of the composition root, and the Shadow row of the mode table) |
| [012](adr-012-continuous-ai-evaluation.md) | Continuous AI evaluation: three loops and a supervisor desk in MVP1; deterministic code trades, AI proposes, the Code Owner decides | Accepted | — |
| [013](adr-013-shadow-composition-root.md) | Shadow composition root lives in `entrypoints/serve.py`; Shadow market data is the REST klines source | Accepted | — |
| [014](adr-014-pre-registration-binds-real-capital.md) | Real-capital timing follows the signed pre-registration — no live capital before the one-shot OOS certification, not before 2027-01-01 | Accepted | — |
| [015](adr-015-read-only-telegram-commands.md) | Read-only Telegram commands — a separate long-polling bot that answers the operator, never trades, and keeps pushed messages blind | Accepted | — |
| [016](adr-016-tax-estimate-rules.md) | Tax estimate rules — per-year savings scale from config (30% top band since 2025), fees in FIFO values, measurement only | Accepted | — |
| [017](adr-017-test-tiers-as-enforced.md) | Test tiers as enforced — pick tiers by path, mutation with cosmic-ray, one table of where each tier runs | Accepted | — |
| [018](adr-018-ci-cd-infrastructure-and-resource-budget.md) | CI/CD infrastructure and resource budget — where runners live, what each may use, what runs when | Accepted | — |
| [019](adr-019-security-baseline-and-threat-model.md) | Security baseline and threat model — what Hermes protects, from whom, and the controls that answer each threat | Accepted | [ADR-020](adr-020-read-only-exchange-query-keys.md) (Decision 2 and §Exchange API keys, "Until MVP2": a read-only query key for contract tests is allowed) |
| [020](adr-020-read-only-exchange-query-keys.md) | Read-only exchange query keys before MVP2 — ban trade-capable keys, allow one query-only key for contract tests | Accepted | — |
| [021](adr-021-strategy-lifecycle-ready-and-done.md) | Strategy lifecycle — Ready and Done rules for every stage, a valid shadow clock for v1, and the rules for a multi-strategy lab | Accepted | — |
| [022](adr-022-release-versions-and-deploy-cadence.md) | Release versions and deploy cadence — cut versions by release PR, tag them on deploy, and deploy only versions once the shadow clock runs | Accepted | — |
| [023](adr-023-backtest-runs-on-server-01.md) | Backtests run on server-01 — one runner, pinned data that fills itself, and a Code Owner click for every Validation touch | Accepted | [ADR-026](adr-026-research-batch-and-signed-champion-rule.md) (§5, a Validation run waits for the Code Owner's click, and §9, `workflow_dispatch` is the only trigger, for strategies signed in a batch) |
| [024](adr-024-four-environments-and-promotion.md) | Four environments, with hermes-01 as staging until a champion earns real money | Accepted | — |
| [025](adr-025-train-decision-replay-on-engine-prs.md) | Engine PRs replay Train and compare decisions, on the CI pool with the klines mounted read-only | Accepted | — |
| [026](adr-026-research-batch-and-signed-champion-rule.md) | Research batches — several signed candidates backtested together, Validation started by the pipeline, and a champion picked by a signed rule | Accepted | — |
| [027](adr-027-zero-major-versions-until-real-capital.md) | Release versions are `0.y.z` until a champion trades real money; `1.0.0-shadow.N` is retired | Accepted | — |
| [028](adr-028-release-cut-every-merge-window.md) | A release is cut at every merge window | Proposed | — |
| [029](adr-029-one-hour-bars-for-intraday-holding-candidates.md) | 1h bars for intraday-holding candidates, in backtest, shadow and prod together | Proposed | — |
| [031](adr-031-research-sweep-for-a-strong-champion.md) | Research sweeps, 100+ signed candidates per batch to find one strong champion | Accepted | — |
<!-- adr-index-end -->
