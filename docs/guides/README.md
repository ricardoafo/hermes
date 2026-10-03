# Guides — how-to prose for humans

Reproducible procedures. Setup, dev workflow, release, operations.
Written for a human operator (Ricardo) or a future collaborator. Not
for machines. Per [ADR-009](../architecture/adr-009-documentation-strategy.md)
§3 (Guides).

## Living index

The table below is **auto-generated** from `docs/guides/*.md` by
`scripts/dev/regenerate-guides-index.sh` (runs as a pre-commit hook +
CI verification). Do NOT edit the table by hand — edit the guide files
themselves, and the index regenerates on next commit.

Why: manual index maintenance caused merge conflicts every time two
parallel PRs added new guides. Auto-generation eliminates that class
of conflict entirely.

<!-- guides-index-start -->
| Guide | What it covers | Last updated |
| ----- | -------------- | ------------ |
| [ai-authored-tests.md](ai-authored-tests.md) | What makes a unit test meaningful, how AI typically fails at writing tests, the protocol AI must follow in this repo, and the tooling that enforces it. | 2026-10-03 |
| [ai-development-lifecycle.md](ai-development-lifecycle.md) | End-to-end flow from idea to VPS runtime; how milestones, issues, and the project board fill in as work moves. | 2026-10-03 |
| [automation-trust-chain.md](automation-trust-chain.md) | How PRs #209 + #210 + #213 combine to give a hands-off "idea → merge to main" flow. Cross-repo reproduction reference. | 2026-10-03 |
| [code-conventions.md](code-conventions.md) | Design patterns, error handling, immutability, factories, async, performance, polyglot posture, testing, time handling, event schema versioning, feature flags, engine pause, migrations, and public/internal API conventions. | 2026-10-03 |
| [dev-workflow.md](dev-workflow.md) | Branching, commits, PRs, merge policy, delete-branch safety, zero-footprint policy. | 2026-10-03 |
| [ledger-migrations.md](ledger-migrations.md) | How ledger schema migrations run, which ones need `hermes migrate`, and how to roll one back from its snapshot. | 2026-10-03 |
| [local-setup.md](local-setup.md) | Clone → install → run tests → open your first PR. One-time setup steps you do once per clone, then never again. | 2026-10-03 |
| [prose-style.md](prose-style.md) | Comments, docstrings, docs, PR/issue bodies — banned words, formatting rules, per-artifact style. | 2026-10-03 |
| [release.md](release.md) | How to tag a release, what the release workflow does, how to | 2026-10-03 |
| [test-tiers.md](test-tiers.md) | Six-tier marker taxonomy, CI pyramid tier assignments, coverage thresholds, and layer-isolation contracts — the single reference subsequent specs cite. | 2026-10-03 |
| [vps-operations.md](vps-operations.md) | SSH access, OS maintenance, Docker + Tailscale + ufw ops, systemd services, troubleshooting. | 2026-10-04 |
<!-- guides-index-end -->

## When to add a guide

- The procedure is repeatable and worth writing down.
- A future-you or future-collaborator needs to reproduce it.
- The procedure is longer than 3 shell commands or requires judgment
  at any step.

## When NOT to add a guide

- One-off setup that only Ricardo will ever do (put it in a script + a
  decision journal entry).
- Documentation of *what* the code does (that's Google-style docstrings
  in code — per ADR-009 §5).
- Architectural rationale (that's an ADR).

## Lifecycle

Guides are **living documents**. Any procedure change PRs the guide in the
same PR that changes the procedure. A guide that drifts from reality is
worse than no guide.
