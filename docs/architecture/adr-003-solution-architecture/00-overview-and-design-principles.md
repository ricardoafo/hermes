# 00 — Overview & Design Principles

> This section surfaces the pattern language and non-negotiable rules that govern every downstream section of ADR-003. Read it first. Every choice in sections 01-10 is defensible against the principles below.

## Design principles (project-wide)

The product owner stated the design constraint on 2026-08-20:

> "We build software with scalable and agnostic view. We base ourselves on design patterns that allow us to build production-level software with the highest quality."

Concretely, this ADR interprets that constraint as **six operating principles**:

1. **Deterministic core, human-in-the-loop periphery.** The strategy engine and execution engine are pure functions of state + config. The LLM never generates trade decisions (see [ADR-001](../adr-001-llm-as-supervisor.md)). Humans confirm irreversible operations.
2. **Scalable within reason, not for hypothetical scale.** Design for a single-operator retail workload (single VPS, ≤50 trades/month, one exchange). Do not build for 1000 users, multi-region, or 10k TPS. Reject patterns whose cost exceeds their benefit at this scale.
3. **Agnostic at the edges, opinionated at the core.** Exchange, notifier, ledger, and clock are behind ports so they can be swapped. Business rules live in one place, expressed once.
4. **Production-level quality from Day 1.** Zero-warnings gate, typed everywhere, deterministic tests, transaction-safe writes, structured logging. No "we'll clean it up later".
5. **Patterns earn their keep.** Every abstraction must justify its complexity at *our* scale. If a pattern doesn't add clarity, testability, or optionality, it's cargo culting.
6. **Reversibility over reversal.** Every write path defaults to dry-run and requires a typed confirmation to commit. When something goes wrong, `hermes reconcile` restores the truth from the exchange.

## Pattern language — applied

Each pattern below is used because it earns its complexity at this scale. Section references point to where the pattern lives in the code.

| Pattern               | Applied to                                                            | Why it earns its keep                                                                         | Detail in  |
| --------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ---------- |
| Ports & Adapters      | Whole codebase (domain ↔ application ↔ adapters)                      | Isolates business rules from I/O; adapters swap freely; domain is unit-testable without mocks | 01         |
| Strategy              | `ExchangePort`, `SignalEngine`, `NotificationChannel`                 | Binance today, Kraken tomorrow, paper-trading always available                                | 04, 05, 06 |
| Value Objects         | `Money`, `Price`, `Weight`, `Pair`, `Quantity`, `LotId`               | Type safety at the boundary; forbids silly errors like `Money + Price`                        | 01, 03     |
| Repository            | `LedgerRepository` (trades, lots, positions)                          | Business layer never sees SQL; test doubles are trivial                                       | 03         |
| Domain Events         | `TradeExecuted`, `RebalanceTriggered`, `AnomalyDetected`, `LotClosed` | Decouples core action from side-effects (notify, log, audit) in-process                       | 02, 06     |
| Configuration as Code | `pydantic-settings` layered from env + YAML + secrets                 | Zero magic strings, typed config, environment-specific overrides                              | 09         |
| Dry-run default       | Every write use case takes `commit: bool = False`                     | A typed `--commit` flag or `Confirm.ask()` is the ONLY path to state change                   | 05, 10     |

## Pattern language — rejected (with reasoning)

The zero-warnings rule and the "patterns earn their keep" principle demand justification for what's NOT here. These were considered and rejected on cost/benefit at our scale.

| Pattern                            | Rejected because                                                                                          | Would revisit if                                                                      |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Full DDD tactical patterns         | Aggregates + Bounded Contexts + Domain Services add ceremony without payoff for a single-model system     | Multi-domain (e.g. trading + accounting + reporting as separate teams)                |
| CQRS                               | Reads and writes share the same SQLite schema; no scale differential; adds sync headaches                 | Read scale >100× write scale, or eventual-consistency requirements                    |
| Event Sourcing                     | SQLite ledger + append-only `trades` table gives us an audit trail; full ES is overkill for solo operator | Regulatory need for full state reconstruction from events, or multi-writer            |
| Microservices                      | One codebase, one process family, one machine. Boundaries add ops cost with zero benefit                  | Multiple deployment units required by different SLAs                                  |
| REST API for trading               | No external consumers; TUI + CLI + Telegram cover all UX; incoming HTTP is attack surface                 | Third-party integrations required                                                     |
| Message bus (Kafka/RabbitMQ)       | In-process events + SQLite outbox pattern cover the delivery guarantees we need                           | Multi-service async communication at scale                                            |
| Kubernetes / Docker Compose        | systemd is the native scheduler on our VPS. Docker adds a layer for zero benefit at N=1                   | Multi-machine or team environments where reproducibility matters more than simplicity |
| ORMs (SQLAlchemy)                  | Schema is small, hand-written SQL is clearer and faster; DTOs at the boundary                             | Schema explodes past ~15 tables OR complex JOINs everywhere                           |
| Metrics stack (Prometheus/Grafana) | structlog + journald + healthchecks.io + Telegram alerts cover the observability need at N=1              | Multi-service, multi-instance, or SLI/SLO commitments                                 |

## Anti-patterns forbidden

These are hard rules. Violation blocks merge.

- **God objects.** Modules over 300 lines get split. Classes with more than one reason to change get decomposed.
- **Direct I/O from the domain layer.** No `binance-connector` imports in `src/hermes/domain/`. Enforced by an import-linter rule.
- **Silencing linters.** Never `# noqa`, `# type: ignore`, or `# markdownlint-disable` without narrow scope + inline reason + PR reviewer sign-off. Blanket suppressions are auto-rejected. Zero-warnings rule is MANDATORY, non-negotiable.
- **Hardcoded configuration.** Any value that could change per environment lives in `config/` or env vars. If you see `0.21` in code for IVA, that's a bug (see section 03).
- **Business logic in adapters.** Adapters translate between protocols; they do not decide. If an adapter branches on business rules, the branch belongs in the application layer.
- **Untyped `dict[str, Any]` as payloads.** Every payload is a `pydantic` model or a dataclass. Freeform dicts are only permitted at the JSON serialization boundary.
- **Trade write without transaction.** Every trade insert and its associated domain event commit in the same SQLite transaction. Partial writes are not recoverable.
- **Trade write without dry-run first.** Every write path exposes a dry-run mode that returns the planned action without side-effects. Callers opt into commit explicitly.

## Reading order

The 10 sections build progressively. Read them in this order for the first pass:

| Order | Section                                                 | Answers the question                                             |
| ----- | ------------------------------------------------------- | ---------------------------------------------------------------- |
| 1     | [01 — Code Organization](./01-code-organization.md)     | Where does code live? What imports what?                         |
| 2     | [02 — Data Flow](./02-data-flow.md)                     | What happens when the rebalancer wakes up? A swing signal fires? |
| 3     | [03 — Persistence & Schema](./03-persistence-schema.md) | What state exists on disk? How is FIFO computed?                 |
| 4     | [04 — Exchange Adapters](./04-exchange-adapters.md)     | How do we talk to Binance? How do we swap to Kraken?             |
| 5     | [05 — Signal Engines](./05-signal-engines.md)           | How does the rebalancer decide? How does Donchian decide?        |
| 6     | [06 — Notifications](./06-notifications.md)             | How do trades reach the phone? What templates?                   |
| 7     | [07 — User Interface](./07-user-interface.md)           | CLI + TUI: what commands, what screens, what keys?               |
| 8     | [08 — LLM Supervisor Layer](./08-llm-supervisor.md)     | How does Claude fit in? What MCP tools exist? What guardrails?   |
| 9     | [09 — Deployment](./09-deployment.md)                   | How does this run on the VPS? What CI/CD? What secrets?          |
| 10    | [10 — Failure & Recovery](./10-failure-recovery.md)     | What breaks? How do we detect it? How do we recover?             |

## Non-negotiable invariants (project-wide)

These are constraints that every section respects. They are not up for debate within the scope of this ADR.

| #    | Invariant                                                                                        | Enforced by                                                       |
| ---- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| I-01 | Zero warnings/errors from any linter — never silence, always resolve                             | pre-commit + CI + PR review                                       |
| I-02 | LLM never generates trade signals; MCP tools are read-only                                       | `.claude/settings.json` permission rules; MCP tool inventory      |
| I-03 | Every write path exposes dry-run mode by default                                                 | `--commit` flag on CLI; typed `commit: bool = False` on use cases |
| I-04 | Import direction enforced: domain → nothing; application → domain; adapters → application/domain | `import-linter` rule in CI                                        |
| I-05 | All configuration typed via `pydantic-settings`, never hardcoded                                 | Import ban on magic strings for known config keys                 |
| I-06 | Every trade write happens in one SQLite transaction with its domain event                        | Reviewer checklist; integration tests                             |
| I-07 | Every user-facing amount displayed with proper IVA breakdown (Spanish tax)                       | Snapshot tests on templates and CLI output                        |
| I-08 | LOPDGDD consent recorded before any personal-data write                                          | Order-of-operations enforced in `create_appointment` use case     |
| I-09 | claude-bot NEVER self-merges; all ADRs / PRs / specs require human approval                      | Branch protection + reviewer allowlist                            |
| I-10 | Every markdown table padded to visually-aligned columns; every fenced block has a language tag   | markdownlint config (MD040, MD055-058, MD060)                     |

## What this section is NOT

- **Not a spec.** Specs live under `openspec/` or engram as SDD artifacts.
- **Not a runbook.** Runbooks live at `docs/runbook/`.
- **Not a research report.** Research lives at `docs/research/`.
- **Not a decision journal.** Small parameter decisions (e.g. "chose 5% rebalance band, not 3%") live at `docs/decisions/`.

## Cross-references

- [ADR-001 — LLM as supervisor](../adr-001-llm-as-supervisor.md) — WHY the LLM is not a trader
- ADR-002 — Tax posture (DGT V2232-25, dynamic FIFO) *(pending)*
- ADR-004 — Exchange decision (Binance primary, Kraken backup, agnostic port) *(pending)*
- ADR-005 — AI/ML support boundary (execution logging, EWMA, SPC, HRP) *(pending)*
- ADR-006 — Versioning strategy *(pending)*
- ADR-007 — Testing strategy *(pending)*
- ADR-008 — Observability strategy *(pending)*
- ADR-009 — Documentation strategy *(pending)*
- ADR-010 — Dependency management + supply chain *(pending)*
