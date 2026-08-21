# Contributing

This is a solo-developer project. Contributions from external collaborators
are rare and require explicit invitation from the owner.

If you have been invited to contribute, read these first, in order:

1. [`docs/guides/dev-workflow.md`](../docs/guides/dev-workflow.md) — the
   full git/gh discipline. Every rule here is enforced by branch
   protection, CODEOWNERS, and CI.
2. [`AGENTS.md`](../AGENTS.md) — the AI collaboration protocol. If you
   are working via Claude Code, Aider, Cursor, or any similar AI tool
   against this repo, this document defines what the AI is allowed to
   do (and what it is not).
3. The relevant [ADR](../docs/architecture/) for whatever you are
   touching. ADRs are the source of truth for architectural decisions.
4. [`SECURITY.md`](../SECURITY.md) if you have found a security bug —
   do NOT open a public issue.

## Quick reference

- **Every change goes through a pull request**. No direct pushes to
  `main` (enforced by branch protection).
- **Issue first, PR second**. Open an issue with the appropriate
  template, then reference it in the PR body via `Closes #N`.
- **Conventional Commits** in every commit message and PR title.
  Enforced by the `commit-msg` pre-commit hook and by CI on PR title.
- **Squash-merge only**. Ricardo reviews and merges everything. Author
  cannot self-approve. Bot cannot merge.
- **Zero warnings from any linter**. Non-negotiable. See ADR-009.
- **Zero footprint of external / unrelated projects** in any repo
  artifact (files, commit messages, PR bodies). See dev-workflow.md
  §Zero-footprint policy.

## Local setup

Assumes Python 3.14 via [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python 3.14 .venv
uv sync                   # installs deps from pyproject.toml (lands in Phase 0)
uv tool install pre-commit
pre-commit install                        # for pre-commit hooks
pre-commit install --hook-type commit-msg # for Conventional Commits validation
```

See [`scripts/dev/`](../scripts/dev/) for repo bootstrap scripts (labels,
milestones, branch protection). Those are for the owner only — not for
contributors.

## Getting help

- Read the relevant guide, ADR, or memory before asking. Most questions
  are already answered somewhere.
- If you are still stuck, open a `question`-labelled issue with as much
  context as you have.
