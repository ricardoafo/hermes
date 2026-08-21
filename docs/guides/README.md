# Guides — how-to prose for humans

Reproducible procedures. Setup, dev workflow, release, operations.
Written for a human operator (Ricardo) or a future collaborator. Not
for machines. Per [ADR-009](../architecture/adr-009-documentation-strategy.md)
§3 (Guides).

## Living index

| Guide | What it covers | Last updated |
| ----- | -------------- | ------------ |
| [dev-workflow.md](dev-workflow.md) | Branching, commits, PRs, merge policy, delete-branch safety, zero-footprint policy | 2026-08-21 |
| [ai-development-lifecycle.md](ai-development-lifecycle.md) | End-to-end flow: idea → issue → PR → CI → merge → deploy; how milestones/issues/project board fill in as work moves | 2026-08-21 |
| [prose-style.md](prose-style.md) | Comments, docstrings, docs, PR/issue bodies — banned words, formatting rules, per-artifact style | 2026-08-21 |
| [vps-operations.md](vps-operations.md) | SSH access, OS maintenance, Docker + Tailscale + ufw ops, systemd services, troubleshooting | 2026-08-21 |

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
