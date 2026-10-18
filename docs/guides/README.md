# Guides — how-to prose for humans

Reproducible procedures. Setup, dev workflow, release, operations.
Written for a human operator (Ricardo) or a future collaborator. Not
for machines. Per [ADR-009](../architecture/adr-009-documentation-strategy.md)
§3 (Guides).

## Living index

The table below is **generated** from `docs/guides/*.md` by
`scripts/dev/regenerate-guides-index.sh`. Run the script in the PR that
adds, removes or retitles a guide and commit the result; do not edit the
table by hand. Pre-commit and CI check that every guide can be indexed
(`--check`), and `tests/unit/test_docs_indexes.py` fails when a guide is
missing from the table or a row names a file that no longer exists.

Why committed: nothing rebuilt the table on `main`, so it went stale (it
still listed a removed guide and missed a new one, issue #1197). Two PRs
that each add a guide can conflict on the table; the second one re-runs the
script after merging `main` in.

<!-- guides-index-start -->
| Guide | What it covers |
| ----- | -------------- |
| [ai-authored-tests.md](ai-authored-tests.md) | What makes a unit test meaningful, how AI typically fails at writing tests, the unit-test standard every test in this repo follows, and the tooling that enforces it. |
| [ai-development-lifecycle.md](ai-development-lifecycle.md) | End-to-end flow from idea to VPS runtime; how milestones, issues, and the project board fill in as work moves. |
| [ai-review.md](ai-review.md) | How the independent AI review works on every PR, what the "For Rick" card means, how writers act on findings, and how the reviewer token is set up. |
| [automation-trust-chain.md](automation-trust-chain.md) | How PRs #209 + #210 + #213 combine to give a hands-off "idea → merge to main" flow. Cross-repo reproduction reference. |
| [ci-runners-and-merge-queue.md](ci-runners-and-merge-queue.md) | Where CI jobs run, how they are routed, and how the merge queue batches, gates and merges approved PRs. |
| [code-conventions.md](code-conventions.md) | Design patterns, error handling, immutability, factories, async, performance, polyglot posture, testing, time handling, event schema versioning, feature flags, engine pause, migrations, and public/internal API conventions. |
| [dev-workflow.md](dev-workflow.md) | Branching, commits, PRs, merge policy, delete-branch safety, zero-footprint policy. |
| [environments.md](environments.md) | Where dev, QA, the staging lab and prod live, how a release moves between them, what each environment may touch, and every machine step the Code Owner runs, with what happens when either machine is down. |
| [ledger-migrations.md](ledger-migrations.md) | How ledger schema migrations run, which ones need `hermes migrate`, and how to roll one back from its snapshot. |
| [local-setup.md](local-setup.md) | Clone → install → run tests → open your first PR. One-time setup steps you do once per clone, then never again. |
| [operator-usage.md](operator-usage.md) | Day-to-day use: connect with `ssh hermes` or `hermes --vps`, read the console commands, see the live release, how auto-deploy works, and where to look when something is wrong. |
| [pr-flow.md](pr-flow.md) | How a PR travels from a branch to main and production, and the rules learned the hard way on 2026-10-03. |
| [prose-style.md](prose-style.md) | Comments, docstrings, docs, PR/issue bodies — banned words, formatting rules, per-artifact style. |
| [release.md](release.md) | How to tag a release, what the release workflow does, how to |
| [severity-rubric.md](severity-rubric.md) | How Hermes ranks a defect or a PR: SEV-1 to SEV-4 levels, the security rows S1-S12 and the functional rows F1-F12 used by bug triage, the `sev-*` labels and the `# SEV-1:` test annotation. |
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
