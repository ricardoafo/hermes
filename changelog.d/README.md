# CHANGELOG fragments

One-bullet-per-PR fragments that `scripts/dev/aggregate-changelog.py` merges
into `CHANGELOG.md [Unreleased]` at release-cut time. Introduced by process
hardening wave 4 (drift-eval `drift-eval/2026-08-22-post-wave2`) to eliminate
the structural CHANGELOG conflict that hit every PR in the #124-#132 queue —
each PR touching the same `[Unreleased] → ### Added` section forces every
non-first merger to rebase.

## Why fragments

Before wave 4, every feature/fix PR edited `CHANGELOG.md` directly. When two
PRs raced to merge, the second one hit a merge conflict on the changelog even
though the PRs had zero code overlap. Fragments give each PR its own file, so
merges only conflict if they touch the exact same feature.

## Naming convention

`<PR-number>-<type>.md`

- `<PR-number>`: the numeric GitHub PR number (e.g. `132`). Chronological
  ordering falls out for free, and there can be no collision between
  concurrent PRs.
- `<type>`: one of `added`, `changed`, `deprecated`, `removed`, `fixed`,
  `security` — matches the Keep-a-Changelog sections.

Examples:

- `132-added.md` — feature added by PR #132
- `140-fixed.md` — bug fix by PR #140
- `145-security.md` — security fix by PR #145

**Placeholder convention**: fragments whose filename does NOT start with a
digit (e.g. `wave-4-process-hardening.md`) are ignored by the aggregator.
This handles the chicken-and-egg case where the PR number is not known until
`gh pr create` returns — the fragment can be committed with a keyword name,
then renamed post-open if needed. Aggregation only counts numeric-prefixed
files.

## Format

Each fragment is a single Markdown bullet (or nested bullets under one
top-level bullet). No section headers — the type comes from the filename.

```markdown
- **Slack notifier adapter** (`src/hermes/adapters/notifier/slack_adapter.py`):
  concrete `NotifierPort` for the reports/rich channel per the 2026-08-23
  decisions-journal entry. Closes #132.
```

The aggregator inserts each fragment as-is under the correct section, in
PR-number order (ascending — oldest first).

## Backwards compatibility

`scripts/dev/changelog-check.sh` (the "CHANGELOG was updated" CI gate) accepts
EITHER a direct edit to `CHANGELOG.md` OR the addition of a fragment file
under `changelog.d/`. Small docs-only PRs may still update the changelog
directly if that's easier — the fragment pattern is the DEFAULT for
feat/fix/refactor/perf, not a hard requirement.

## Aggregation

At release cut:

```bash
uv run python scripts/dev/aggregate-changelog.py --apply
```

- With `--apply`: rewrites the `[Unreleased]` block of `CHANGELOG.md` in
  place, merging all fragments under their sections.
- Without `--apply`: prints the merged `[Unreleased]` block to stdout for
  review.

After a successful release cut, delete the aggregated fragments:

```bash
rm changelog.d/*-{added,changed,deprecated,removed,fixed,security}.md
```

Placeholder-named fragments (non-numeric prefix) are NOT deleted — they
are visible only to humans reading the directory.
