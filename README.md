# Project Hermes

Solo-developer crypto trading system.
Deterministic rule engine, hexagonal architecture, Spanish tax posture
(FIFO + Modelo 100/721), LLM supervision via MCP at Phase 3+.

**Status**: Phase 0 (scaffold). No production runtime yet.

## What lives where

| Surface | Purpose |
| ------- | ------- |
| [`docs/architecture/`](docs/architecture/) | ADRs 001-010 (accepted 2026-08-21). Source of truth for architectural WHAT and HOW. |
| [`docs/guides/`](docs/guides/) | Reproducible procedures (dev workflow, ops, release). |
| [`docs/decisions/`](docs/decisions/) | Journal of small operational choices that don't warrant an ADR. |
| [`docs/runbook/`](docs/runbook/) | Incident recovery playbooks (populated as incidents happen). |
| [`docs/research/`](docs/research/) | Digests of external material that fed decisions. |
| [`AGENTS.md`](AGENTS.md) | Project-specific AI collaboration protocol (10 absolute prohibitions + behavioral defaults). |
| [`CHANGELOG.md`](CHANGELOG.md) | Release notes (Keep a Changelog format per ADR-006). |
| [`SECURITY.md`](SECURITY.md) | How to report security issues. |
| [Project board](https://github.com/users/ricardoafo/projects/4) | Roadmap by phase (0-3), open work items, issue-to-PR flow. |

## Roadmap

Four phases, one per milestone. See the [project board](https://github.com/users/ricardoafo/projects/4)
for current status.

| # | Milestone | What lands |
| --- | --------- | ---------- |
| 0 | Scaffold | `pyproject.toml`, hexagonal `src/hermes/` skeleton, CI, testing scaffold, structlog, supply-chain, doc surfaces |
| 1 | Simulation | Paper-trading engine, FIFO ledger, backtest harness, deterministic replay, strategy pattern |
| 2 | Shadow | Tiny real capital, live orders with typed confirmations, execution logging, 90-day observation |
| 3 | Live | Full production, LLM supervisor via MCP, monthly reconciliation, Modelo 100/721 pipeline |

## Working on the code

Read these first, in order:

1. [`docs/guides/dev-workflow.md`](docs/guides/dev-workflow.md) — branching, commits, PRs, merge policy
2. [`AGENTS.md`](AGENTS.md) — if you (or your AI assistant) will open PRs on this repo
3. Relevant [ADR](docs/architecture/) for whatever you are touching

Every change goes through a PR. Ricardo Afonso reviews and merges everything.
No self-approval, no bot merges, no force-push to `main`. See dev-workflow.md
for the full discipline.

## No license (yet)

This repository is proprietary — all rights reserved by the copyright holder.
No license file is present. If you are reading this and are not Ricardo
Afonso, you do not have permission to use, copy, modify, or distribute this
code. A license may be added in a future phase if the project goes public.
