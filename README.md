# Project Hermes

Solo-developer crypto trading system.
Deterministic rule engine, hexagonal architecture, Spanish tax posture
(FIFO + Modelo 100/721), LLM supervision via MCP in MVP2+.

**Status**: MVP1 in progress. Hermes runs as a paper-trading engine on live public
market data on the `hermes-01` host, which is the staging environment until a
champion strategy finishes its shadow test ([ADR-024](docs/architecture/adr-024-four-environments-and-promotion.md)).
No exchange key is installed and no order is sent: real capital is MVP2.

## What lives where

| Surface | Purpose |
| ------- | ------- |
| [`docs/architecture/`](docs/architecture/) | Architecture decision records (ADRs), numbered in order. Source of truth for architectural WHAT and HOW. |
| [`docs/guides/`](docs/guides/) | Reproducible procedures (dev workflow, ops, release). |
| [`docs/decisions/`](docs/decisions/) | Journal of small operational choices that don't warrant an ADR. |
| [`docs/runbook/`](docs/runbook/) | Incident recovery playbooks (populated as incidents happen). |
| [`docs/research/`](docs/research/) | Digests of external material that fed decisions. |
| [`AGENTS.md`](AGENTS.md) | Project-specific AI collaboration protocol (13 absolute prohibitions + behavioral defaults). |
| [`CHANGELOG.md`](CHANGELOG.md) | Release notes (Keep a Changelog format; versions per [ADR-022](docs/architecture/adr-022-release-versions-and-deploy-cadence.md) and [ADR-027](docs/architecture/adr-027-zero-major-versions-until-real-capital.md)). |
| [`SECURITY.md`](SECURITY.md) | How to report security issues. |
| [Project board](https://github.com/users/ricardoafo/projects/4) | Roadmap by milestone, open work items, issue-to-PR flow. |

## Roadmap

Two active milestones. See the [project board](https://github.com/users/ricardoafo/projects/4)
for current status.

| Milestone | What lands |
| --------- | ---------- |
| **MVP1 — Simulation ready** | Paper-trading engine, FIFO ledger, backtest + calibration harness, shadow-mode flag, Binance testnet + Kraken demo adapters, notifications live, ledger + tax guard operational |
| **MVP2 — Live trading** | Real money on Binance + Kraken mainnet, warm-standby VPS, full observability, LLM supervisor via MCP, monthly reconciliation, Modelo 100/721 pipeline |

## Install and run

Python 3.14, managed by [`uv`](https://github.com/astral-sh/uv). The full first-clone
checklist (git config, hooks, bot identity) is in
[`docs/guides/local-setup.md`](docs/guides/local-setup.md).

```bash
git clone https://github.com/ricardoafo/hermes.git
cd hermes
uv sync --frozen
uv run hermes --help
uv run pytest tests/unit
```

Operating the live host (connect, read the console, deploys):
[`docs/guides/operator-usage.md`](docs/guides/operator-usage.md).

## Working on the code

Read these first, in order:

1. [`docs/guides/dev-workflow.md`](docs/guides/dev-workflow.md) — branching, commits, PRs, merge policy
2. [`AGENTS.md`](AGENTS.md) — if you (or your AI assistant) will open PRs on this repo
3. Relevant [ADR](docs/architecture/) for whatever you are touching

Every change goes through a PR. The Code Owner reviews and approves everything, and the
merge queue merges approved PRs once CI is green ([`docs/guides/pr-flow.md`](docs/guides/pr-flow.md)).
No self-approval, no bot merges, no force-push to `main`. See dev-workflow.md
for the full discipline.

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
