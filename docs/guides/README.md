# Guides — how-to prose for humans

Reproducible procedures. Setup, dev workflow, release, operations.
Written for a human operator (Ricardo) or a future collaborator. Not
for machines. Per [ADR-009](../architecture/adr-009-documentation-strategy.md)
§3 (Guides).

## Living index

The table below is **auto-generated** from `docs/guides/*.md` by
`scripts/dev/regenerate-guides-index.sh`, rebuilt on `main` after each
merge. Do NOT edit the table in a PR, by hand or by running the script —
edit the guide files themselves. Pre-commit and CI only check that the
index still builds (`--check`).

Why: when PRs committed the table, two PRs adding guides conflicted, and
a committed copy went stale whenever another PR merged first.

<!-- guides-index-start -->
| Guide | What it covers |
| ----- | -------------- |
| [ai-authored-tests.md](ai-authored-tests.md) | What makes a unit test meaningful, how AI typically fails at writing tests, the protocol AI must follow in this repo, and the tooling that enforces it. |
| [ai-development-lifecycle.md](ai-development-lifecycle.md) | End-to-end flow from idea to VPS runtime; how milestones, issues, and the project board fill in as work moves. |
| [ai-review.md](ai-review.md) | How the independent AI review works on every PR, what the "For Rick" card means, how writers act on findings, and how the reviewer token is set up. |
| [automation-trust-chain.md](automation-trust-chain.md) | How PRs #209 + #210 + #213 combine to give a hands-off "idea → merge to main" flow. Cross-repo reproduction reference. |
| [ci-runners-and-merge-queue.md](ci-runners-and-merge-queue.md) | Where CI jobs run, how they are routed, and how the merge queue batches, gates and merges approved PRs. |
| [code-conventions.md](code-conventions.md) | Design patterns, error handling, immutability, factories, async, performance, polyglot posture, testing, time handling, event schema versioning, feature flags, engine pause, migrations, and public/internal API conventions. |
| [dev-workflow.md](dev-workflow.md) | Branching, commits, PRs, merge policy, delete-branch safety, zero-footprint policy. |
| [ledger-migrations.md](ledger-migrations.md) | How ledger schema migrations run, which ones need `hermes migrate`, and how to roll one back from its snapshot. |
| [local-setup.md](local-setup.md) | Clone → install → run tests → open your first PR. One-time setup steps you do once per clone, then never again. |
| [operator-usage.md](operator-usage.md) | Day-to-day use: connect with `ssh hermes` or `hermes --vps`, read the console commands, see the live release, how auto-deploy works, and where to look when something is wrong. |
| [pr-flow.md](pr-flow.md) | How a PR travels from a branch to main and production, and the rules learned the hard way on 2026-10-03. |
| [prose-style.md](prose-style.md) | Comments, docstrings, docs, PR/issue bodies — banned words, formatting rules, per-artifact style. |
| [release.md](release.md) | How to tag a release, what the release workflow does, how to |
| [staging-on-hermes-01.md](staging-on-hermes-01.md) | How the staging tier runs beside prod on hermes-01 without sharing its user, paths, ledger or secrets, and every manual step to set it up, deploy to it, roll it back and remove it. |
| [tax-pack.md](tax-pack.md) | What the yearly tax filing pack holds, how it refreshes during the year, when it freezes, and how to reopen a frozen year. |
| [test-tiers.md](test-tiers.md) | The single table of test tiers: what each one is, which workflow or command runs it, how often, and whether it is live or planned; plus coverage floors and layer-isolation contracts. |
| [vps-operations.md](vps-operations.md) | SSH access, OS maintenance, Docker + Tailscale + ufw ops, systemd services, troubleshooting. |
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
